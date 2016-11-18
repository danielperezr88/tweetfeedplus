from tweepy.streaming import StreamListener

from watson_developer_cloud import AlchemyLanguageV1

from flask import abort
from flask_socketio import emit

from dateutil import parser
import regex as re
import json

from collections import deque

from threading import Event

from google.protobuf import timestamp_pb2
from gcloud import storage

import os

CONFIG_BUCKET = 'configs-hf'

# Descargamos el dataset de cancer del bucket de datasets
client = storage.Client()
cblob = client.get_bucket(CONFIG_BUCKET).get_blob('tweetfeedplus_config.py')
fp = open(os.path.join('app','tweetfeedplus_config.py'),'wb')
cblob.download_to_file(fp)
fp.close()

import tweetfeedplus_config as conf

alchemy_language = AlchemyLanguageV1(api_key=conf.api_key)

class MyListener(StreamListener):
    def __init__(self, count, socketio, stopper):
        self.count = count
        self.queue = deque(maxlen=int(count))
        self.socketio = socketio
        self.stopper = stopper

        self.last_data_time = int(parser.datetime.datetime.now().strftime("%d"))
        self.limit = dict(day=self.last_data_time, limit=0)
        self.limit_path = os.path.join('app', 'api_use_limit.json')

        existed = os.path.exists(self.limit_path)
        if existed:
            self.load_limits()
        if self.limit['day'] != self.last_data_time or not existed:
            self.limit = dict(day=self.last_data_time, limit=0)
            self.update_limits(self.last_data_time)

    def load_limits(self):
        fp = open(self.limit_path, 'rb')
        self.limit = json.load(fp)
        fp.close()

    def update_limits(self):
        fp = open(self.limit_path, 'wb')
        json.dump(self.limit, fp)
        fp.close()

    def on_data(self, tweet):

        tweet = json.loads(tweet)

        try:

            tdy = int(parser.datetime.datetime.now().strftime("%d"))
            if self.last_data_time != tdy:
                self.last_data_time = tdy
                self.limit = dict(day=self.last_data_time, limit=0)
                self.update_limits()

            sentiment = 'unknown'
            score = 0
            if self.limit['limit'] < 1000:
                alchemy_res = alchemy_language.sentiment(text=tweet['text'])
                if alchemy_res['status'] == 'OK':
                    sentiment = alchemy_res['docSentiment']['type']
                    score = alchemy_res['docSentiment']['score']
                self.limit['limit'] += 1
                self.update_limits()

            emoji_pattern = re.compile(
                u"[\u0100-\uFFFF\U0001F000-\U0001F1FF\U0001F300-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001FFFF\U000FE000-\U000FEFFF]+",
                flags=re.UNICODE)

            date_object = parser.parse(tweet['created_at'])
            date_str = date_object.strftime("%Y-%m-%d %H:%M:%S")

            content = emoji_pattern.sub(r'', tweet['text'])
            id_str = tweet['id_str']

            user_name = emoji_pattern.sub(r'', tweet['user']['screen_name'])
            user_id_str = tweet['user']['id_str']
            user_img = tweet['user']['profile_image_url']

            location = emoji_pattern.sub(r'', str(tweet['user']['location']))

            lang = tweet['lang']

            lon, lat = (tweet['coordinates']['coordinates'][0], tweet['coordinates']['coordinates'][1]) \
                if tweet['coordinates'] is not None else (0, 0)

            is_rt = 'retweeted_status' in tweet
            rt_lat = \
            rt_lon = \
            rt_from = None
            rt_loc = \
            rt_fromstr = ''
            rt_cnt = 0
            if is_rt:
                rt_from = emoji_pattern.sub(r'', tweet['retweeted_status']['user']['screen_name'])
                rt_loc = emoji_pattern.sub(r'', str(tweet['retweeted_status']['user']['location']))
                rt_fromstr = tweet['retweeted_status']['user']['id_str']
                rt_lat, rt_lon = (tweet['retweeted_status']['coordinates']['coordinates'][0],
                                              tweet['retweeted_status']['coordinates']['coordinates'][1]) if \
                                              tweet['retweeted_status']['coordinates'] is not None else (0, 0)
                rt_cnt = tweet['retweet_count']

            self.queue.append(dict(content=content, user_name=user_name, user_img=user_img, location=location, lang=lang,
                                   lon=lon, lat=lat, date_str=date_str, is_rt=is_rt, rt_from=rt_from, rt_loc=rt_loc,
                                   rt_lat=rt_lat, rt_lon=rt_lon, rt_cnt=rt_cnt, sentiment=sentiment, score=score))

        except BaseException as e:
            pass

        if len(self.queue) == self.count:
            self.socketio.emit('tweet_data',
                               dict(data=[self.queue.pop() for i in range(self.count)]),
                               namespace='/streampeek_socket')

        return not self.stopper.is_set()

    def on_error(self, status):
        if status == 401:
            self.socketio.emit('not_valid_token', namespace='/streampeek_socket')
            return False
        print(status)
        return True
