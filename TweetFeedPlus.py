from eventlet.patcher import monkey_patch

from flask import Flask, session, request, redirect, render_template, url_for, json, get_flashed_messages, flash, abort
from flask_socketio import SocketIO, emit

import werkzeug.exceptions as ex

from threading import Event

from tweepy import Stream
from tweepy import OAuthHandler
from TweetStreaming import MyListener

from os import urandom, path
from binascii import hexlify

import requests as req

from hashlib import sha512

from google.protobuf import timestamp_pb2
from gcloud import storage

ID_BUCKET = 'ids-hf'

# Descargamos el dataset de cancer del bucket de datasets
client = storage.Client()
cblob = client.get_bucket(ID_BUCKET).get_blob('tweetfeedplus_ids.py')
fp = open(path.join('app', 'tweetfeedplus_ids.py'), 'wb')
cblob.download_to_file(fp)
fp.close()

from tweetfeedplus_ids import id_dict as ids

monkey_patch()
socketio = SocketIO()


def generate_url(host, protocol='http', port=80, dir=''):

    if isinstance(dir, list):
        dir = '/'.join(dir)

    return "%s://%s:%d/%s" % (protocol, host, port, dir)


class StreamHandler:
    def __init__(self, socketio):

        self.streams = []
        self.threads = []
        self.stops = []
        self.sio = socketio

    def launch_stream(self, ckey, csec, atkey, atsec, terms, qty=5, languages=['es']):

        auth = OAuthHandler(ckey, csec)
        auth.set_access_token(atkey, atsec)

        stopper = Event()
        self.stops.append(stopper)
        listener = MyListener(qty, self.sio, stopper)

        idx = len(self.streams)
        self.streams.append(Stream(auth, listener))

        self.threads.append(self.sio.start_background_task(target=self.streams[idx].filter,track=terms, languages=languages))

        return idx

    def fetch_listener(self, idx, qty):
        results = []
        for i in range(qty):
            results += self.streams[idx].listener.pop()

        return results

    def idx_stop(self, idx):
        self.stops[idx].set()
        return self.stops[idx].is_set()

    def __del__(self):
        for s in self.streams:
            del s


class NotAllowed(ex.HTTPException):
    code = 403
    description = 'This is not the page you are looking for.'


class NotValidToken(ex.HTTPException):
    code = 401
    description = 'Invalid Twitter Token'

abort.mapping[401] = NotValidToken
abort.mapping[403] = NotAllowed


def no_impostors_wanted():
    if (not session['logged_in']) if 'logged_in' in session.keys() else False:
        abort(403)


#MYIP = '127.0.0.1'
MYIP = req.get(generate_url('jsonip.com')).json()['ip']

app = Flask(__name__, static_url_path="", static_folder='static')
flask_options = dict(port=50100, host='0.0.0.0')
def run():
    app.secret_key = hexlify(urandom(24))
    socketio.init_app(app, message_queue='amqp://localhost:5672')
    socketio.run(app, **flask_options)

handler = StreamHandler(socketio)


@app.route('/')
def root():
    return redirect(url_for('index'))


@app.route('/index')
def index():
    return render_template('index.html')


@app.route('/logout')
def logout():
    del session['username']
    session['logged_in'] = False
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uname = request.form['username']
        if uname in ids.keys():
            if ids[uname] == sha512(bytes(request.form['password'], encoding='latin1')).hexdigest():
                session['username'] = request.form['username']
                session['logged_in'] = True
                return redirect(url_for('index'))
            flash('Password did not match that for the login provided')
            return render_template('login.html')
        flash('Unknown username')
    return render_template('login.html')


@app.route('/new_stream', methods=['GET'])
def new_stream():
    no_impostors_wanted()
    return render_template('new_stream.html')


@app.route('/streampeek', methods=['POST'])
def streampeek():
    post_data = request.form

    no_impostors_wanted()

    if 'terms' in post_data.keys():
        if len(post_data['terms']) > 0:

            qty = post_data['qty'] if 'qty' in post_data.keys() else 5

            return render_template('streampeeker.html', qty=qty, ckey=post_data['consumer_key'],
                                   csec=post_data['consumer_secret'], atkey=post_data['access_token_key'],
                                   atsec=post_data['access_token_secret'], terms=post_data['terms'].split(','),
                                   languages=post_data['languages'].split(','))

    flash('Any term given. Please provide one...')
    return redirect(url_for('new_stream'))


@socketio.on('create', namespace='/streampeek_socket')
def streampeek_socket_connect(message):

    global handler

    no_impostors_wanted()

    index = handler.launch_stream(message['ckey'], message['csec'], message['atkey'], message['atsec'],
                                  message['terms'], message['qty'], message['languages'])

    emit('thread_created', {'index': index}, namespace='/streampeek_socket')


@socketio.on('remove', namespace='/streampeek_socket')
def streampeek_socket_disconnect(message):
    global handler

    no_impostors_wanted()

    if handler.idx_stop(message['index']):
        emit('success_remove', namespace='/streampeek_socket')
    else:
        emit('remove_failure', namespace='/streampeek_socket')


@socketio.on('connect', namespace='/streampeek_socket')
def test_connect():
    emit('success_connect', namespace='/streampeek_socket')


@socketio.on('check_alive', namespace='/streampeek_socket')
def check_alive():
    emit('im_alive', namespace='/streampeek_socket')


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(403)
def this_is_not_the_page_you_are_looking_for(e):
    return render_template('403.html'), 403


@app.errorhandler(401)
def not_valid_twitter_token(e):
    flash(e.description)
    return redirect(url_for('new_stream'))

if __name__ == '__main__':
    run()
