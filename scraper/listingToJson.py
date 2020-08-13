####################################################################################################
# BLOG Parser and Scraper
# Copyright (c) 2019-2020 Marco Zafra
# 
# Program flow:
# 1. Parse a master list of expected blog posts, listed by place name
# 2. Retrieve direct URLs to blog posts by searching Google
# 3. Retrieve the blog post and scrape its content
#
# TODO: Rely on the blog API to expose all places instead of relying on a
# prewritten master list.
#
# The program flow is such because the blog author maintains a PDF of
# the restaurants they have reviewed. For my convenience, I used the PDF
# to write a master list of places in Markdown format.
#
# NOTE: The blog name is redacted for now with strings 'BLOG', 'blog', 'bl_', and 'example.com'
# pending author's permission to use their content
####################################################################################################

import argparse
from slugify import slugify
from pprint import pprint
from gsearch.googlesearch import search
from google import google
from ratelimiter import RateLimiter
from unidecode import unidecode
from bs4 import BeautifulSoup
import pickle
import os
import ftfy
import json
import urllib
import datetime
import dateutil.parser, dateutil.tz
import re
import requests
import markdown
import copy

class BLOGParser(object):
    _spoof_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/72.0.3626.109 Safari/537.3'

    def __init__(self, args: argparse.Namespace):
        self._raw_file_uri = args.fileuri
        self._out_uri = args.out
        self._export_path = args.export
        self._reset_state()
        self._clear_documents()

    ################################################################################################
    # RESET MASTER LIST PARSER
    ################################################################################################

    def _reset_state(self):
        self._state = None # 'cuisine', 'location'
        self._substate = None # 'name', 'fields', 'description'
        self._document = None # committed when switching states
        self._cuisine_slug = None # current cuisine slug when iterating thru locations

        if not hasattr(self, '_raw_file_uri'):
            self._raw_file_uri = None
        if not hasattr(self, '_out_uri'):
            self._out_uri = None
        if not hasattr(self, '_export_path'):
            self._export_path = None

    def _clear_documents(self):
        self.cuisines = [] # list of document dicts
        self.locations = [] # list of document dicts

    ################################################################################################
    # UTILITY METHODS
    ################################################################################################

    def _rate_limiter_callback(self, until):
        print('    Rate limited until: {}'.format(until))

    def _get_slug_from_url(self, url: str):
        # expecting https://example.com/index.php/restaurant-name/
        if not 'index.php/' in url:
            raise ValueError('Unrecognized URL format. Expected: https://x.com/index.php/slug-here/')

        if url.endswith('/'):
            url = url[:-1]
        return url.split('/')[-1]

    def _get_link_status(self, href: str):
        status = None
        try:
            print('    Checking URL {}'.format(href))
            status = requests.head(href, headers={'User-Agent': self._spoof_agent}).status_code
        except Exception as e:
            print('    Warning: Could not check URL {}\n    {}'.format(href, e))

        if status is None or status >= 400:
            print('    Warning: URL {} returned status {}'.format(href, status))

        return status

    def _make_datetime(self, dtstr, dtstr_gmt = None):
        # if local datetime and GMT datetime are specified (wordpress REST), make the output timezone-aware
        dt_out = dateutil.parser.parse(dtstr)
        if dtstr_gmt:
            tzdelta = dt_out - dateutil.parser.parse(dtstr_gmt)
            dt_out = dt_out.replace(tzinfo=dateutil.tz.tzoffset('ET', tzdelta.total_seconds()))
        return dt_out

    ################################################################################################
    # MASTER LIST PARSER LOGIC
    ################################################################################################

    def parse(self):
        with open(self._raw_file_uri) as f:
            for line in f:
                # ignore one-hash lines ('# Restaurants') because
                # they are a document header
                if line.startswith('## '):
                    self._end_state()
                    self._state = 'cuisine'
                elif line.startswith('### '):
                    self._end_state()
                    self._state = 'location'

                self._parse_line(line)
            self._end_state()

    def _parse_line(self, line: str):
        # handle parsing for both cuisine and location states. They're mostly the same, except
        # where noted.
        #
        # parse rules: we can't look forward or backward a line, just read the current line.
        # a new substate is established while we already operate on the first line of that substate.

        if self._substate == 'name':
            if self._state == 'cuisine':
                name_prefix = '## '
            else:
                name_prefix = '### '

            # get entity name and generate slug
            if line.startswith(name_prefix):
                # location names are part of the raw locator ("### name, street address, city, phone number")
                # so just get the beginning segment before the first comma
                # then slice off the name_prefix, then trim whitespace
                self._document['name'] = line.split(',', 1)[0][len(name_prefix):].strip()

                # transform name to lower-dash-case and set that as slug
                # this will be overwritten by a subsequent '* ' field line if one exists
                # (e.g., "* slug: slug-name")
                self._document['slug'] = slugify(self._document['name'])

                # if location, also record the entire line as a raw locator
                if self._state == 'location':
                    self._document['rawContactPoint'] = line[len(name_prefix):].strip()
            else:
                if len(line.strip()) > 0:
                    if line.startswith('* '):
                        self._substate = 'fields'
                    else:
                        self._substate = 'description'
        # fallthru because the current line may already be the start of a new substate

        if self._substate == 'fields':
            if line.startswith('* '):
                # strip '* ' from line ([2:]), then split 'key: val', then trim whitespace from both segments
                split_line = [segment.strip() for segment in line[2:].split(':', 1)]

                # special case: value of 'cuisines' is a string array
                if split_line[0] == 'cuisines':
                    self._document[split_line[0]] = [segment.strip() for segment in split_line[1].split(',')]
                # special case: if value is True or False, evaluate as boolean
                elif split_line[1].lower() == 'true':
                    self._document[split_line[0]] = True
                elif split_line[1].lower() == 'false':
                    self._document[split_line[0]] = False
                else:
                    self._document[split_line[0]] = split_line[1]
            else:
                if len(line.strip()) > 0:
                    self._substate = 'description'
        # fallthru again

        # after '* ' field lines, record subsequent non-three-hash lines as description
        if self._substate == 'description':
            self._document.setdefault('description', '')
            self._document['description'] += line # + '\n'

    def _end_state(self):
        # TODO fill in default missing fields

        # cuisine cleanup: store new cuisine if slug does not already exist
        if self._state == 'cuisine':
            # record current cuisine slug for subsequent locations
            self._cuisine_slug = self._document['slug']

            # if slug does not already exist, commit the new document
            slug_found = False
            for doc in self.cuisines:
                if doc['slug'] == self._document['slug']:
                    print('Notice: Cuisine slug {} already exists, not recording...'.format(self._document['slug']))
                    slug_found = True
            if not slug_found:
                self.cuisines.append(self._document)

        elif self._state == 'location':
            # add current cuisine slug to cuisines array
            self._document.setdefault('cuisines', []).append(self._cuisine_slug)

            # if slug does not already exist, commit the new document
            slug_found = False
            for doc in self.locations:
                if doc['slug'] == self._document['slug']:
                    print('Notice: Location slug {} already exists, not recording...'.format(self._document['slug']))
                    slug_found = True
            if not slug_found:
                self.locations.append(self._document)

        # re-init vars to initial values
        self._substate = 'name'
        self._document = {}

    ################################################################################################
    # BLOG Handling
    ################################################################################################

    def find_blog(self, force: bool = False, check_duplicates: bool = True, use_abenassi: bool = False, use_manual: bool = False):
        rate_limiter = RateLimiter(max_calls=1, period=55, callback=self._rate_limiter_callback)

        # page-scraping -- on each [ { location } ]
        for doc in self.locations:
            doc.setdefault('url', {})

            if not force and 'blog' in doc['url']:
                print('Location {} already has a BLOG URL; skipping...'.format(doc['slug']))
                continue

            print('Location {}: Finding BLOG URL...'.format(doc['slug']))

            # search google for "site:example.com {description}" (truncate to first paragraph)

            # strip markdown
            # fix broken UTF-8 with ftfy, or the mojibake â€“ throws off google
            # then strip the accents with unidecode
            desc_html = markdown.markdown(unidecode(ftfy.fix_encoding(doc['description'].split('\n', 1)[0])))
            desc = "".join(BeautifulSoup(desc_html).findAll(text=True))

            search_text = 'site:example.com {} {}'.format(
                unidecode(ftfy.fix_encoding(doc['name'])), desc)
            alt_search_text = 'site:example.com {}'.format(
                unidecode(ftfy.fix_encoding(doc['name'])))

            # set { url.blog } to first result that does NOT have /page/ in it
            def parse_results(results):
                for result in results:
                    if use_manual:
                        result_url = result
                    elif use_abenassi:
                        result_url = result.link
                    else:
                        result_url = result[1]

                    # skip pagination results
                    if '/page/' in result_url:
                        continue

                    # check if URL already exists
                    if check_duplicates:
                        for check_doc in self.locations:
                            if doc is check_doc:
                                break
                            if 'url' in check_doc and 'blog' in check_doc['url']:
                                if check_doc['url']['blog'] == result_url:
                                    print('Warning: Location {} has the same BLOG URL as {}'.format(doc['slug'], check_doc['slug']))

                    # record first search result
                    doc['url']['blog'] = result_url
                    print('    {}'.format(doc['url']['blog']))
                    break

            # do the searching
            if use_manual:
                search_query = '? {}'.format(search_text)
                print('\n{}\n'.format(search_query))
                results = [input('Result URL: ')]
            else:
                with rate_limiter:
                    if use_abenassi:
                        num_page = 1
                        results = google.search(search_text, num_page)
                    else:
                        results = search(search_text, num_results=10)

            parse_results(results)

            # if no luck, try it again by searching just the name
            if not 'blog' in doc['url'] and not use_manual:
                with rate_limiter:
                    if use_abenassi:
                        num_page = 1
                        results = google.search(alt_search_text, num_page)
                    else:
                        results = search(alt_search_text, num_results=10)
                parse_results(results)

            # still no luck, then give up (e.g., Sushi Taro)
            if not 'blog' in doc['url']:
                print('Warning: Location {} could not find a BLOG URL'.format(doc['slug']))

    def download_blog(self, force: bool = False):
        rate_limiter = RateLimiter(max_calls = 1, period=37, callback=self._rate_limiter_callback)

        for doc in self.locations:
            if not 'url' in doc or not 'blog' in doc['url'] or not isinstance(doc['url']['blog'], str) or len(doc['url']['blog']) == 0:
                print('Location {} does not have a BLOG url; skipping...'.format(doc['slug']))
                continue

            print('Location {}: downloading BLOG page...'.format(doc['slug']))

            target_slug = self._get_slug_from_url(doc['url']['blog'])
            scrape_url = 'http://example.com/wp-json/wp/v2/posts?slug={}'.format(target_slug)

            if force or not 'blogData' in doc:
                with rate_limiter:
                    req = urllib.request.Request(scrape_url, headers={'User-Agent': self._spoof_agent})
                    f = urllib.request.urlopen(req)
                    try:
                        doc['blogData'] = json.load(f)
                        self.dump()
                    except:
                        print('Warning: Location {} could not retrieve BLOG JSON'.format(doc['slug']))

    def scrape_blog(self, force: bool = False):
        # TODO if specified, replace doc['description'] with data['content']['rendered'] after some transforms
        # transforms: drop first p; remove youtube embeds; save img locally (and strip from body?)

        youtube_regex = re.compile(r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?(?P<id>[A-Za-z0-9\-=_]{11})')

        for doc in self.locations:
            doc.setdefault('content', {})

            # make article content parseable
            try:
                data = doc['blogData'][0]
                soup = BeautifulSoup('<html><body>{}</body></html>'.format(data['content']['rendered']), 'html.parser')
            except Exception as e:
                print('Location {} does not have BLOG data; skipping...'.format(doc['slug']))
                print('    {}'.format(e))
                continue

            print('Location {}: scraping BLOG data...'.format(doc['slug']))

            # ignore all links under <ul class="related_post">
            try:
                soup.find('ul', class_='related_post').decompose()
            except Exception:
                pass

            # process a tags
            a_elems = soup.find_all('a')
            doc['content'].setdefault('a', [])

            for elem in a_elems:
                href = elem['href']

                # TODO check for broken link for ['url']['yelp'] and ['url']['maps']?
                # currently we only do this for ['content']['a']
                # status = self._get_link_status(href)

                # on the BLOG URL, look for yelp link, set { url.yelp } to yelp link
                if 'yelp.' in elem['href']:
                    if force or 'yelp' not in doc['url']:
                        doc['url']['yelp'] = elem['href']
                # ignore Google Plus
                elif 'plus.google.' in elem['href']:
                    pass
                # TODO: ignore google map link in the future; grab from Yelp or geolocate ourselves
                # for now, store google map link
                elif 'google.' in elem['href'] and 'maps' in elem['href']:
                    if force or 'maps' not in doc['url']:
                        doc['url']['maps'] = elem['href']
                        # also store the link text as rawAddress, since that's most often what it is
                        doc['rawAddress'] = elem.string
                # ignore "Metro Trip Planner"
                elif elem['href'].endswith('wmata.com/') or elem['href'].endswith('wmata.com') or (elem.string is not None and 'Metro Trip Planner' in elem.string):
                    pass
                # if url is link to an image AND the innerHTML is the same <img> tag, then ignore
                elif elem.find('img'):
                    pass
                # for all other links, record { [ url.extras = { name: LinkText, locator: URL }]}
                else:
                    # record link, but only if it doesn't already exist
                    found_href = False
                    found_i = -1
                    for i, entry in enumerate(doc['content']['a']):
                        if entry['href'] == elem['href']:
                            found_href = True
                            found_i = i
                            break
                    if force and found_href:
                        del doc['content']['a'][found_i]
                    if force or not found_href:
                        # check for broken link (HTTP status)
                        # TODO: Follow 3xx redirects and get actual URL
                        status = self._get_link_status(href)
                        doc['content']['a'].append({'name': elem.string, 'href': elem['href'], 'statusCode': status})

                # remove google maps link if yelp already exists
                # scratch: we might need this to look for google places
                #if 'yelp' in doc['url'] and 'maps' in doc['url']:
                #    doc['url'].pop('maps', None)

            # process img tags
            img_elems = soup.find_all('img')
            doc['content'].setdefault('img', [])

            for elem in img_elems:
                # download and save to doc['img'][0,1,2...]{filename: x.jpg, contentType: image/jpeg, data: base64}
                pass

            # for every iframe (or <a> youtube link)
            # record youtube watch ID
            #doc['content'].setdefault('video', [])
            #match = youtube_regex.match(youtube_url)
            #if match:
            #    youtube_id = match.group('id')

            # other tasks

            # change native title to wordpress title
            doc['name'] = data['title']['rendered']

            # record datetime with timezone (subtract date - date_gmt)
            self._make_datetime(data['date'], data['date_gmt'])
            doc['date'] = self._make_datetime(data['date'], data['date_gmt']).isoformat()
            doc['modified'] = self._make_datetime(data['modified'], data['modified_gmt']).isoformat()

            # record wordpress id to { id: { 'blog': 3333 }}
            doc.setdefault('id', {})
            doc['id']['blog'] = data['id']

            # TODO get categories and tags
            # TODO get comments

            # save progress
            self.dump()

    ################################################################################################
    # DUMP TO FILE
    ################################################################################################

    def dump(self, filename: str = None):
        if filename is None:
            if self._out_uri is None:
                filename = os.path.join(os.path.dirname(self._raw_file_uri), 'out.pickle')
            else:
                filename = self._out_uri
        pickle.dump(bl_parser, open(filename, 'wb'))
        print('Wrote output to "{}"'.format(filename))

    def export(self, pathname: str = None, export_blogData: bool = False):
        if pathname is None:
            if self._export_path is None:
                pathname = os.path.dirname(self._raw_file_uri)
            else:
                pathname = self._export_path

        # export locations
        location_uri = os.path.join(pathname, 'locations.json')
        locations = copy.deepcopy(self.locations)

        # strip blogData if specified
        if not export_blogData:
            for doc in locations:
                doc.pop('blogData', None)

        json.dump(locations, fp=open(location_uri, 'w'))
        print('Exported cuisine output to "{}"'.format(location_uri))

        # export cuisines
        cuisine_uri = os.path.join(pathname, 'cuisines.json')
        json.dump(self.cuisines, fp=open(cuisine_uri, 'w'))
        print('Exported cuisine output to "{}"'.format(cuisine_uri))

def get_args():
    parser = argparse.ArgumentParser(description='Convert Markdown listing file to BLOG JSON documents')
    parser.add_argument('fileuri', type=str, help='Path to input text file')
    parser.add_argument('--load', '-l', type=str, help='Path to existing state file')
    parser.add_argument('--out', '-o', type=str, help='Path to output PICKLE file')
    parser.add_argument('--export', '-e', type=str, help='Folder path to JSON exports')
    parser.add_argument('--abenassi', '-a', action='store_true', help='Use abenassi/Google-Search-API instead of aviaryan/python-gsearch for Google scraping')
    parser.add_argument('--manual-google', '-m', dest='manual', action='store_true', help='Input Google search result manually when scraping')
    args = parser.parse_args()
    return args

def main():
    args = get_args()

    if args.load:
        bl_parser = pickle.load(open(args.load, 'rb'))
        bl_parser._reset_state()
    else:
        bl_parser = BLOGParser(args)

    # Parse master listing and retrieve content from BLOG
    # TODO: Do not overwrite stored content if the remote content is not newer
    bl_parser.parse()
    bl_parser.find_blog(use_abenassi=args.abenassi, use_manual=args.manual)
    bl_parser.download_blog()
    bl_parser.scrape_blog()

    ##print('='*80+'\n'+'CUISINES'+'\n'+'='*80+'\n')
    ##pprint(bl_parser.cuisines)

    ##print('\n' + '='*80+'\n'+'LOCATIONS'+'\n'+'='*80+'\n')
    ##pprint(bl_parser.locations)

    bl_parser.export()
    bl_parser.dump(args.out)

if __name__ == '__main__':
    main()
