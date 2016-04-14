#!/usr/bin/env python
# -*- coding: utf-8 -*-

import collections
import csv
import eventlet
from helpers import *
from itertools import chain, izip
import logging
import json
import os
from progressbar import AnimatedMarker, Bar, BouncingBar, Counter, ETA, FileTransferSpeed, FormatLabel, Percentage, ProgressBar, ReverseBar, RotatingMarker, SimpleProgress, Timer
from StringIO import StringIO
import sys
import time
import urllib
import urllib2
import yaml
import zipfile

logging.basicConfig(level=logging.INFO)
termsize = map(lambda x: int(x), os.popen('stty size', 'r').read().split())

#TODO: logging config
print __file__
print os.path.realpath(__file__)

def read_config(config_file='%s/conf/%s_conf.yaml' %(os.path.dirname(__file__),os.path.splitext(os.path.basename(os.path.realpath(__file__)))[0])):
    print config_file
    with open(config_file, 'r') as f:
        return yaml.load(f)

config=read_config()

class Feed(object):
    @staticmethod
    def stream_iter(response):
        size = 64 * 1024
        while True:
            slice = response.read(size)
            if not slice: break
            yield slice

    @staticmethod
    def download_feed(feed_name,feed_url,stream_out,basename,timestamp='Sat, 29 Oct 1994 19:43:31 GMT',timeout=10,user_agent='Mozilla/5.0 (Linux i686)'):
        #print('-'*termsize[1])
        eventlet.monkey_patch()
        with eventlet.Timeout(timeout):
            accepted='application/zip', 'application/x-zip-compressed'
            request = urllib2.Request(feed_url, headers={'User-agent': user_agent, 'Accept': ','.join(accepted), 'If-Modified-Since': timestamp})
            response = urllib2.urlopen(request)
            
        if response.info().get('content-type') not in accepted:
            logging.error('Wrong Content Type for url %s', feed_url)
            raise TypeError('Wrong Content Type for feed "%s" (url: "%s"). Expected type "%s" was %s' %(feed_name,feed_url,','.join(accepted),response.info().get('content-type')))
        content_length=int(response.info().get('content-length'))
        last_modified=response.info().get('last-modified')
        
        if response.getcode() not in (200, 301, 302, 304):
            logging.error('HTTP error code %i', response.getcode())
            return

        if response.getcode() == 304:
            return
        
        #no content
        if content_length is None:
            logging.error('Empty File for url "%s"', feed_url)
            raise TypeError('Empty File for url %s' %feed_url) 
        else:
            widgets = ['%s (%s->%s):'%(feed_name,feed_url,basename), Percentage(), ' ', Bar(marker=RotatingMarker()),
                       ' ', ETA(), ' ', FileTransferSpeed()]
            pbar = ProgressBar(widgets=widgets, maxval=content_length).start()
            pbar.start()
            
            dl = 0
            for data in Feed.stream_iter(response):
                dl += len(data)
                pbar.update(dl)
                stream_out.write(data)
            stream_out.flush()
            pbar.finish()
        return last_modified
    
class TransitFeedAPI(object):
    def __init__(self,key,limit,feed_url,user_agent='Mozilla/5.0 (Linux i686)'):
        self.__key=key
        self.__limit=limit
        self.__feed_url=feed_url
        self.__user_agent=user_agent
        
    def get_feeds_from_page(self, page=1):
        feed_args = { 'key': self.__key, 'page': page, 'limit': self.__limit}
        url = '%s?%s'%(self.__feed_url,urllib.urlencode(feed_args))

        request = urllib2.Request(url, headers={'User-agent': self.__user_agent})
        response=urllib2.urlopen(request)
        
        if response.getcode() != 200:
            logging.error('API query was unsucessful')
            return [],0,0
        try:
            feeds=json.loads(response.read())
            with open('test_%i.json' %page, 'w') as f:
                json.dump(feeds,f)
        except ValueError, e:
            logging.error('API query was unsucessful. (url: %s, Error: %s)', url, e)
            raise ValueError('API query was unsucessful. (url: %s, Error: %s)' %(url, e))

        if feeds['status'] != 'OK':
            logging.error('Result from API was NOT ok.')
            raise RuntimeError('API query was unsucessful. Response %s' %feeds)
            
        if self.__limit != feeds['results']['limit']:
            logging.warning('Limits for transit feed url do not match (set limit = %i, returned limit = %i)', self.__limit, feeds['results']['limit'])

        timestamp=time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(time.time()))
        return feeds['results']['feeds'],feeds['results']['page'],feeds['results']['numPages'], timestamp

    def get_all_feeds(self):
        try:
            results=list()
            results_json,_,num_pages,timestamp=self.get_feeds_from_page(page=1)
            results.extend(results_json)
            for i in xrange(2,num_pages+1):
                results.extend(self.get_feeds_from_page(page=i)[0])
            results.sort()
            return results, timestamp
        except urllib2.HTTPError, e:
            if e.code == 401:
                logging.error('API key was probably wrong. Request returned %s', e)
            else:
                logging.error('Request returned %s', e)
            return []
        
    @staticmethod
    def _header_value_mappings(feed):
        d=collections.OrderedDict()
        d['api_id'] = ['id']
        d['name'] = ['t']
        d['url'] = ['u','d']
        d.update({e: ['l',e] for e in feed['l'].keys()})
        return d
    
    @staticmethod
    def _dataset(feed,m,timestamp):
        ret = [nested_get(feed,v) for v in m.itervalues()]
        ret.append(timestamp)
        return ret

    def _feeds2list(self):
        #download list of feeds from API
        logging.info('Downloading list of feeds from transitfeed API at %s', self.__feed_url)
        feeds,timestamp=self.get_all_feeds()
        mapping=self._header_value_mappings(feeds[0])
        #initialize with header
        ret = [mapping.keys() + ['downloaded']]
        #put feeds into list
        #TODO: error handling for some items
        for feed in chain_list(feeds):
            ret.append(self._dataset(feed,mapping,timestamp))
        return ret
    
    def get_all_feeds_dict(self):
        L=self._feeds2list()
        return [{k: v for k,v in izip(L[0],e)} for e in L[1:]]

    @staticmethod
    def _feedlist2csv(L,stream=StringIO()):
        csvwriter = csv.writer(stream, delimiter=';')
        for line in L:
            #unicode encoding for commandline output
            line = map(lambda x: x.encode('utf8') if type(x) is unicode else x, line)
            csvwriter.writerow(line)
        return stream

    def get_all_feeds_as_csv(self,stream=StringIO()):
        L=self._feeds2list()
        return self._feedlist2csv(L,stream)

from normalize_gtfs_archive import FeedArchive
class FeedList(object):
    def __init__(self,key,url,path='./feeds',overwrite=False,datafile='./conf/data.yaml',timeout=10,user_agent='Mozilla/5.0 (Linux i686)'):
        self.__api=TransitFeedAPI(key=key, limit=100,feed_url=url)
        self.__overwrite=overwrite
        path=os.path.realpath(path)
        if not os.path.exists(path):
            os.makedirs(path)
        self.__path=path
        self.__user_agent=user_agent
        self.__timeout=timeout
        self.__datafile=datafile
        #TODO: try to read data file
        if os.path.isfile(datafile):
            with open(datafile, 'r') as d:
                self.data=yaml.load(d)
        else:
            self.data = dict()
        
    def get_feeds(self):
        return self.__api.get_all_feeds_dict()
    
    def get_feeds_csv(self):
        return self.__api.get_all_feeds_as_csv().getvalue()
    
    def save_feed(self,feed_name,feed_url,filename,if_modified_since):
        if not feed_url or feed_url == 'NA':
            logging.error('URL missing for feed "%s" (url "%s").', feed_name, feed_url)
            return False, 'Missing url', None
            
        if not self.__overwrite and os.path.isfile(filename):
            logging.error('File "%s" does already exists. Skipping.', filename)
            return True, 'File exists.', None
        try:
            #download feed
            with open(filename, 'wb') as stream_out:
                #timestamp=time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(time.time()))
                try:
                    last_modified=Feed.download_feed(feed_name,feed_url,stream_out, os.path.basename(filename), if_modified_since, self.__timeout, self.__user_agent)
                except TypeError, e:
                    logging.warning('No zip file for feed "%s" (url "%s") found.', feed_name, feed_url)
                    return False, 'NoZip', None
                except eventlet.timeout.Timeout, e:
                    logging.warning('Connection timeout error for feed "%s" (url "%s"). Error was: %s', feed_name, feed_url, e)
                    return False, 'Connection timeout', None
                except urllib2.URLError, e:
                    try:
                        if e.code == 304:
                            logging.info('File "%s" for feed "%s" (url "%s") is up-to-date.', os.path.basename(filename), feed_name, feed_url)
                            return True, 'File "%s" for feed "%s" (url "%s") is up-to-date.' %(filename, feed_name, feed_url), if_modified_since
                    except AttributeError, e:
                        pass
                    logging.warning('Connection error for feed "%s" (url "%s"). Error was: %s', feed_name, feed_url, e)
                    return False, 'Connection error', None

                #normalize feed
                arch=FeedArchive(filename)
                try:
                    arch.normalize(replace=True)
                except zipfile.BadZipfile, e:
                    logging.warning('Badzip for feed "%s" (url "%s" downloaded.', feed_name, feed_url)
                    return False, 'BadZip', last_modified
                return True, 'OK', last_modified
        except KeyboardInterrupt, e:
            logging.warning('CTRL+C hit. Stopping download. Marking download as unsuccessful.')
            return False, 'User abort.', None
            
                
    def save_all_feeds(self):
        f=self.get_feeds()
        widgets = ['Processed: ', Counter(), ' of %i feeds (' %len(f), Timer(), ')']
        pbar = ProgressBar(widgets=widgets,maxval=len(f))
        processed = self.data
        #f[1:]
        for feed in pbar(f[1:]):
            #check whether feed is in datafile
            d=self.data.get(feed['api_id'])
            if_modified_since=d.get('last_modified') if d and d.get('successful') else 'Thu, 01 Jan 1970 00:00:00 GMT'
            filename='%s/%s.zip' %(self.__path,feed['name'].replace(' ', '_').replace('/','-'))
            successful,msg,last_modified=self.save_feed(feed['name'],feed['url'],filename,if_modified_since)
            if not successful:
                logging.warning('Removing incomplete file.')
                if os.path.exists(filename):
                    os.remove(filename)
            processed[feed['api_id']] = {'successful': successful, 'msg': msg, 'last_modified': last_modified, 'filename': filename}
            with open(self.__datafile,'w') as outfile:
                yaml.dump(processed,outfile, allow_unicode=True)

import httpretty

httpretty.enable()
def request_callback(request, uri, headers):
    with open('test_%s.json' %request.querystring['page'][0]) as f:
        data = json.load(f)
        return (200,headers, json.dumps(data))

httpretty.register_uri(httpretty.GET, config['feed_url'],body=request_callback,content_type='text/json')
                       # responses=[httpretty.Response(content_type='text/json',body='',status=200),
                       #            httpretty.Response(content_type='text/json',body='',status=200)])
#httpretty.disable()

#TODO: test
#TODO: write 

o=FeedList(key=config['key'],url=config['feed_url'],path=config['feed_path'],overwrite=True,timeout=config['timeout'],user_agent=config['user_agent'])
#o.save_feed('my_feed',"http://data.cabq.gov/transit/gtfs/google_transit.zip",'test.zip')
o.save_all_feeds()

#get feed list                
#feedlist2csv(L)

#headers={"If-Modified-Since": timestamp}
#304: not modified

#save feed list

#update feed list

#convert feed
#update feed list
#exit(1)


# Running: Example 9
# Working: |

