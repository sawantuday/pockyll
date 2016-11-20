#!/usr/bin/env python
'''
Pockyll - generate Jekyll linkposts from pocket items
'''
from __future__ import print_function
import os
import io
import sys
import datetime
import webbrowser
import yaml
import requests

from pocket import Pocket
from readability import Document
from lxml.html import fromstring

# TODO:
# 1. DONE - Add summarization algorithm (or may be check description tag for ready to eat summary)
# 2. DONE - Collect tags from the article that can be used in Jekyll
# 3. Move content to _post rather _post/linkPosts
# 4. May be add pagination
# 5. DONE - Check if the title in front matter can be escaped for quotes and other such characters
# 6. Add new dependencies to setup.py
# 7. Find all assets and download them to local machine
# 8. Complete partial URLs with domain from article source
# 9. Categorize articles
# 10. Title needs to be considered/given more importance in keyword/tag extraction 

def usage():
    usage_text = '''
    pockyll - generate Jekyll linkposts from pocket items

    Usage: pockyll <-h|--help|init|auth|sync>

    Commands:
        --help  Show this help dialog
        init    Create an empty _pockyll.yml config file
        auth    Authenticate the application against the pocket OAuth API
        sync    Create Jekyll linkposts from pocket items

    '''
    print(usage_text)

def get_config_filename():
    return os.getcwd() + '/_pockyll.yml'

def create_config():
    '''
    Creates a `_pockyll.yml` with default values in the current working
    directory.
    '''
    default_config = {
        'pocket_consumer_key': None,
        'pocket_redirect_uri': None,
        'pocket_access_token': None,
        'pocket_sync_tags': ['blog'],
        'pocket_since': None,
        'linkpost_post_dir': '_posts/linkposts',
        'linkpost_draft_dir': '_drafts/linkposts'}
    save_config(default_config)

def save_config(config, filename=get_config_filename()):
    '''
    Saves the configuration to a YAML file.
    '''
    configfile = io.open(filename, 'w', encoding='utf8')
    yaml.dump(config, configfile)
    configfile.close()

def load_config(filename=get_config_filename()):
    '''
    Loads the the configuration from a YAML file and returns
    a the configuration as a ``dict`` object.
    '''
    try:
        configfile = io.open(filename, 'r', encoding='utf8')
        config = yaml.load(configfile)
        configfile.close()
    except IOError as e:
        raise RuntimeError('Could not open the configuration file %s. '
                           'Are you in the correct directory and/or did you '
                           'run `pockyll init` prior to the current '
                           'command?' % filename)
    return config

def auth(config):
    '''
    Interactive OAuth authentication against the pocket OAuth API. Generates a
    Pocket authentication URL and directs the users webbrowser to the URL to
    authenticate pocket access for the app.

    Upon successful authentication, the function stores the `access_token` in
    the pockyll config file.
    '''
    # make sure the config is complete
    pocket_consumer_key = config.get('pocket_consumer_key', None)
    pocket_redirect_uri = config.get('pocket_redirect_uri', None)
    if pocket_consumer_key is None or pocket_redirect_uri is None:
        raise RuntimeError(
            "You need to provide pocket_consumer_key and pocket_redirect_uri "
            "in the pockyll configuration file.")
    request_token = Pocket.get_request_token(
        consumer_key=pocket_consumer_key,
        redirect_uri=pocket_redirect_uri)
    auth_url = Pocket.get_auth_url(
        code=request_token,
        redirect_uri=pocket_redirect_uri)
    # start the interactive part
    print('Directing your browser to authenticate against Pocket.')
    print('Please continue authentication in your browser.')
    print('When finished, press ENTER.')
    # Open web browser tab to authenticate with Pocket
    webbrowser.open(auth_url)  # this also works in a text shell
    # Wait for user to hit ENTER before proceeding
    raw_input()
    access_token = Pocket.get_access_token(
        consumer_key=pocket_consumer_key,
        code=request_token)
    # update the config file
    config['pocket_access_token'] = access_token
    save_config(config)
    return config

def get_list(config):
    '''
    Requests the list of items tagged with `tags` since `since`,
    sorted from newest to oldest, irrespective of their read state,
    using the short/simple JSON reprensentation.
    '''
    instance = Pocket(config.get('pocket_consumer_key'),
                      config.get('pocket_access_token'))
    tags = config.get('pocket_sync_tags', 'all')
    since = config.get('pocket_since', None)
    items_list = instance.get(state='all',
                              tag=tags,
                              sort='newest',
                              since=since,
                              detailType='simple')
    return items_list

def get_meta_desc(html):
    '''
    Extract meta description from HTML content
    '''
    tree = fromstring(html)
    desc = tree.xpath('//meta[@name="description"]/@content')   # try description tag
    if not desc:
        desc = tree.xpath('//meta[@name="og:description"]/@content')   # try og:description tag
    if not desc:
        desc = tree.xpath('//meta[@name="twitter:description"]/@content')   # try og:description tag    
    return desc

def get_doc_summary(html, url):
    '''
    Parse document text and extract summary with summarization 
    algorithms. This is helpful when meta-desc tag is not available
    '''
    from sumy.parsers.html import HtmlParser
    # from sumy.parsers.plaintext import PlaintextParser
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.summarizers.text_rank import TextRankSummarizer as Summarizer
    from sumy.nlp.stemmers import Stemmer
    from sumy.utils import get_stop_words

    LANGUAGE = "english"
    SENTENCES_COUNT = 3

    parser = HtmlParser.from_string(html, url, Tokenizer(LANGUAGE))
    # or for plain text files
    # parser = PlaintextParser.from_file("document.txt", Tokenizer(LANGUAGE))
    stemmer = Stemmer(LANGUAGE)

    summarizer = Summarizer(stemmer)
    summarizer.stop_words = get_stop_words(LANGUAGE)

    res = ""
    for sentence in summarizer(parser.document, SENTENCES_COUNT):
        res += str(sentence)
    return res

def get_doc_keywords(html, articleDom):
    '''
    Search meta keyword tag for any predefined keywords
    Else use RAKE library to extract keywords from document content
    Return first five keywords 
    '''
    tree = fromstring(html)
    keywords = tree.xpath('//meta[@name="keywords"]/@content')
    if keywords:
        arr = keywords.split(',')[:5] 	# return first five keywords
        return [x.strip(' ') for x in arr]
    else:
        # Use RAKE to extract keywords from article contetnt
        from RAKE import Rake
        import operator
        node = fromstring(articleDom)
        text = node.text_content()
        extractor = Rake("RAKE/stoplists/SmartStoplist.txt", 3, 3, 5) # min 3 chars, max 3 words, word appears min 5 times
        keywords = [x[0] for x in extractor.run(text)]
        keywords = keywords[:5] # get top five 
        return [x.strip(' ') for x in keywords]

def create_linkpost(config, item_id, title, url, timestamp, is_draft=True):
    path = ''
    if not is_draft:
        path = config.get("linkpost_post_dir", "_posts/linkposts")
    else:
        path = config.get("linkpost_draft_dir", "_drafts/linkposts")

    # Check if path exists    
    if not os.path.exists(path):
        raise RuntimeError(
            "The linkpost destination path %s does not exist. Please "
            "double-check spelling and create the destination path if "
            "applicable." % path)

    # Create file for this post
    linkfilename = "%s/%s-%s.markdown" % (
        path, timestamp.strftime('%Y-%m-%d'), item_id)

    # Skip if file exists
    if os.path.exists(linkfilename):
        raise IOError('Doggedly refusing to overwrite existing file: %s' %
                      linkfilename)

    # Get parsed contents from article
    response = requests.get(url)
    doc = Document(response.text)
    content = doc.summary(True)
    summary = get_meta_desc(response.text)
    if not summary:
        summary = get_doc_summary(response.text, url)
    keywords = get_doc_keywords(response.text, content)

    linkfile = io.open(linkfilename, 'w', encoding='utf8')
    text = '''---
layout: post
type: 'reference'
title: %s
date: %s
ref: %s
excerpt: %s
---

%s

[View Original](%s)
''' % (title, timestamp.strftime('%Y-%m-%dT%H:%M:%S%z'), url, summary, content, url)

    # Write to file and close
    linkfile.write(text)
    linkfile.close()


def sync(config):
    print('Requesting new items from Pocket API...')
    if config.get('pocket_access_token', None) is None:
        raise RuntimeError("Please authenticate the app before syncing.")
    response = get_list(config)
    # [0] is the result, [1] is the HTTP return conde and headers
    bookmarks = response[0]['list']
    n_items = len(bookmarks)
    if n_items > 0:
        print('Syncing %d items.' % n_items)
        n_skipped = 0
        n_drafts = 0
        # pull relevant info from the API response
        # TODO: support tags
        for key, item in bookmarks.items():
            # make sure we have an URL and and item ID
            url = item.get('given_url', None)
            item_id = item.get('resolved_id', None)
            if not all([url, item_id]):
                print('Skipping incomplete item: %s' % str([item_id, url]))
                n_skipped = n_skipped + 1
                continue
            # pull the remaining data
            # check if we have a proper title
            title = item.get('resolved_title', None)
            is_draft = False
            if title in [None, '']:
                is_draft = True
                n_drafts = n_drafts + 1
            # supply a current timestamp, if necessary
            tmp = item.get('time_added', None)
            timestamp = None
            if tmp is not None:
                timestamp = datetime.datetime.utcfromtimestamp(
                    long(item['time_added']))
            else:
                timestamp = datetime.datetime.now()
            # create the linkpost
            try:
                msg = "Linking to POSTs:  %s" % str([title, url, item_id])
                if is_draft:
                    msg = "Linking to DRAFTs: %s" % str([title, url, item_id])
                create_linkpost(config, item_id, title,
                                url, timestamp, is_draft)
                print(msg)
            except IOError as e:
                print("Skipping: %s" % e.message)
        # update timestamp
        config['pocket_since'] = response[0]['since']
        save_config(config)
        print('Done (%d posts/%d drafts/%d skipped).' %
              (n_items - n_drafts - n_skipped, n_drafts, n_skipped))
    else:
        print('No new bookmarks. Done.')

def main(argv=None):
    if argv is None:
        argv = sys.argv
    try:
        # the argument set is so simple that argparse is overkill
        if len(argv) != 2:
            raise RuntimeError('Wrong number of arguments.')
        command = argv[1]
        if command == 'init':
            create_config()
        elif command == 'auth':
            auth(load_config())
        elif command == 'sync':
            sync(load_config())
        elif command in ['-h', '--help']:
            usage()
        else:
            raise RuntimeError('Invalid command')
    except Exception as e:
        print('ERROR: %s' % e.message, file=sys.stderr)
        usage()
        exit(1)

if __name__ == '__main__':
    main(sys.argv)
    exit(0)
