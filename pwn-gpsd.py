#!/usr/bin/env python3

import logging
import socket, select, time
from datetime import datetime
import operator
import os
import sys
import json
import getopt
import random

import hashlib
import base64
from cryptography.fernet import Fernet


from statistics import mean
from urllib.request import urlopen
import urllib
import urllib.parse
import urllib.request

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts

from pwnagotchi.bettercap import Client as bettercap
from pwnagotchi import grid as pwngrid

#
# Feature Creep
#
# [ ] share database with wardriver plugin
# [ ] connect to gpsd to use real GPS when possible
# [ ] weight AP locations by relative RSSI
#          worse_rssi = sorted(aps, key=operator['rssi'])[-1]['rssi']  # a big negative number
#          weight = this_ap[rssi] - worst_rssi          # smaller negative minus big negative = positive
# [ ] 

class PWN_GPSD:
    def __init__(self, host, port, watch=False, password="Friendship"):
        try:
            self.host = host
            self.port = port
            self.watch = watch
            self.socket = None
            self.stream = None
            self.password = password
            
            self.connect()
        except Exception as e:
            self.socket = None
            self.stream = None

    def generate_key(self):
        """Generate a Fernet key from a password"""
        if self.password:
            ekey = hashlib.sha256(self.password.encode()).digest()
            return base64.urlsafe_b64encode(ekey)
        else:
            return None
    
    def encrypt_data(self, obj):
        """Encrypts a message with a password."""
        f = Fernet(self.generate_key())
        data = json.dumps(obj)
        encrypted_message = f.encrypt(data.encode()).decode()
        logging.debug("Encrypted to %s" % encrypted_message)
        return encrypted_message

    def decrypt_data(self, encrypted_message, default=None):
        """Decrypts a message with a password."""
        if encrypted_message:
            f = Fernet(self.generate_key())
            decrypted_message = f.decrypt(encrypted_message.encode()).decode()
            try:
                return json.loads(decrypted_message)
            except Exception as e:
                logging.error("Got text not json: %s" % decrypted_message)
                return decrypted_message
        else:
            return default

    def connect(self):
      try:
        if self.socket:
            logging.info("Closing old socket %s" % repr(self.socket))
            self.socket.close()
            self.socket = None

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((self.host, self.port))
        self.stream = self.socket.makefile(mode="rw")

#        self.stream.write('?WATCH={"enable":true};\n')
#        self.stream.flush()
        #self.socket.send('?WATCH={"enable":true};\n'.encode(encoding="utf-8"))


      except Exception as e:
          logging.exception("Connect error: %s" % repr(e))

    def read(self):
        try:
            if not self.socket:
                logging.warning("Reconnecting to read")
                self.connect()

            self.raw = self.stream.readline()
            logging.debug("Read: %s" % (self.raw.strip()))

#            self.data = json.loads(self.raw)
#            if self.data.get('class', None) == "VERSION":
#                self.stream.write('?WATCH={"enable":true};\n')
#                self.stream.flush()
#                self.write('?DEVICES;\n')
                
            
            return self.raw
        except Exception as e:
            logging.exception("Read error: %s" % e)
            self.socket.close
            raise

    def write(self, data):
        logging.info("Writing %s to gpsd" % data.replace("\n", "\n\t").strip())
        ret = self.stream.write(data)
        self.stream.flush()
        return ret

class PWN_GPSClient:
    def __init__(self, socket, address):
        self.socket = socket
        self.address = address
        self.watch = {}
        self.stream = self.socket.makefile(mode="rw")

    def read(self):
        try:
            self.raw = self.stream.readline()
            logging.info("%s read '%s'" % (self.address, self.raw))
            try:
                if self.raw != "":
                    self.data = json.loads(raw)
            except Exception as e:
                self.data = {}
            
            return self.raw
        except Exception as e:
            logging.exception("Read error %s: %s" % (self.address, self.raw))
            raise

class PWN_GPSD(plugins.Plugin):
    __author__ = 'Sniffleupagus'
    __version__ = '1.0.0'
    __license__ = 'GPL3'
    __description__ = 'A plugin to use wigle data to determine location, and emulate GPSD to pass location data to bettercap and plugins.'

# config.toml:
#
# main.plugins.pwn-gpsd.enabled = true
# main.plugins.pwn-gpsd.wigle_api_key = "Your encoded for use Wigle API key"

    
    def __init__(self):
        # initialize variables
        self._ui_elements = []
        self._current_location = None
        self._aps = []
        self._known_aps = {}
        self._gpsd_socket = None      # connect to a local gpsd, if there is one
        self._server_socket = None    # gpsd partial repeater socket
        self._wigle_timeout = 0       # pause wigle lookups if we get throttled
        logging.debug("PWN-GPSD created")
        

    # called when http://<host>:<port>/plugins/<plugin>/ is called
    # must return a html page
    # IMPORTANT: If you use "POST"s, add a csrf-token (via csrf_token() and render_template_string)
    #def on_webhook(self, path, request):
    #    pass

    # called when the plugin is loaded
    def on_loaded(self):
        logging.debug("PWN-GPS loaded! options = %s " % repr(self.options))
        #if 'wigle_api_key' not in self.options:
        #    logging.warn("No Wigle API Key specified. An API key is needed to request data from Wigle. See https://api.wigle.net for more information, and put the 'Encoded for use' key in config.toml as main.plugins.pwn_gpsd.wigle_api_key")

    # called before the plugin is unloaded
    def on_unload(self, ui):
        for element in self._ui_elements:
            try:
                ui.remove_element(element)
            except Exception as e:
                logging.error(e)

    # called when the agent refreshed an unfiltered access point list
    # this list contains all access points that were detected BEFORE filtering
    def on_unfiltered_ap_list(self, agent, access_points):
        # update internal list of APs. Use unfiltered list for location estimation
        self._aps = access_points.copy()
        self.updateLocation()
        
    def getFileBasename(self, hostname, mac):
        def normalize(name):
            """
            Keep only alphnumerics
            """
            # special cases
            if not name or name == '':
                name = 'EMPTY'
            return (''.join(c for c in name if c.isalnum()))

        return "_".join([normalize(hostname), normalize(mac.lower())])
        
    def updateLocation(self):
        """Sort APs by RSSI. Look for GPS data for the strongest APs in /root/handshakes/*.gps.json. Average the top
        few lat/long to estimate current location."""
        # sort APs by RSSI
        lats = []
        longs = []
        sorted_aps = sorted(self._aps, key=operator.itemgetter( 'rssi'), reverse=True)
        worst_rssi = -100  # sorted_aps[len(sorted_aps)-1]['rssi'] - 1
        weighted_lat = 0
        weighted_lon = 0
        total_weight = 0
        now = time.time()
        
        for ap in sorted_aps:
            logging.debug("Lookup AP: %s" % repr(ap))

            if ap['rssi'] < -100: # not really nearby, so don't include in locating
                return
            last_seen = ap.get('last_seen', '')
            if last_seen != '':
                last_seen = last_seen[0:18] + last_seen[-6:]
                logging.debug("Last seen: %s" % (last_seen))
                
                try:
                    age = now - time.mktime(time.strptime(last_seen, '%Y-%m-%dT%H:%M:%S%z'))
                except ValueError as e:
                    logging.error("Format mismatch: %s" % last_seen)
                    age = 0
                except Exception as e:
                    logging.exception(e)
                    age = 0
                logging.debug("Last seen: %s (%d)" % (last_seen, age))
            else:
                logging.info("NO LAST SEEN!!!")
                age = 0

            if age > 150:
                logging.debug("Oude AP (%s): %s" % (age, repr(ap)))
                break

            basename = self.getFileBasename(ap['hostname'], ap['mac'])
            fname = "/root/handshakes/%s.gps.json" % basename
            logging.debug("Looking for %s" % basename)

            weight = ap['rssi'] - worst_rssi

            if basename in self._known_aps: # already looked this one up
                try:
                    lats.append(self._known_aps[basename]['Latitude'])
                    longs.append(self._known_aps[basename]['Longitude'])

                    total_weight += weight
                    weighted_lat += weight * self._known_aps[basename]['Latitude']
                    weighted_lon += weight * self._known_aps[basename]['Longitude']                    
                    
                    logging.debug("Known %s, weight %d: %0.4f, %0.4f" % (basename,weight,
                                                            self._known_aps[basename]['Latitude'],
                                                            self._known_aps[basename]['Longitude']))
                except Exception as e:
                    logging.exception(e)
            elif os.path.isfile(fname):
                try:
                    gps_data = {}
                    with open(fname, 'r') as f:
                        try:
                            gps_data = json.load(f)
                        except Exception as e:
                            logging.warn(e)
                            break
                    lats.append(gps_data['Latitude'])
                    longs.append(gps_data['Longitude'])
                    self._known_aps[basename] = gps_data

                    total_weight += weight
                    weighted_lat += weight * self._known_aps[basename]['Latitude']
                    weighted_lon += weight * self._known_aps[basename]['Longitude']                    
                    
                    logging.info("Loading %s weight %d: %0.4f %0.4f" % (basename, weight,
                                                                        gps_data['Latitude'],
                                                                        gps_data['Longitude']))
                except Exception as e:
                    logging.exception("Error on %s: %s" % (fname, e))
                
        # average the positions to get new location
        if len(lats):
            self._current_location = {"Latitude": mean(lats), "Longitude": mean(longs), "Stations": len(lats)}
            logging.info("New position: %s" % repr(self._current_location))
            if (total_weight != 0):
                logging.info("Weighted position %0.4f %0.4f" % (float(weighted_lat)/total_weight, float(weighted_lon)/total_weight))
            else:
                logging.info("Weighted position %0.4f %0.4f" % (float(weighted_lat), float(weighted_lon)))

        # send json updates to clients
        # generate SAT update using locations of APs used for estimate
        # generate TPV update with estimated values
        
    
    # called hen there's internet connectivity
    def on_internet_available(self, agent):
        #
        # This is the function that does wigle lookups. It needs a lot of work.
        # it will burn your daily API calls in a matter of minutes, and could
        # risk a permanent ban from Wigle.
        #
        # I have it in here while developing, but this "return" will keep it
        # from doing anything
        #
        # DO NOT ENABLE THIS VERSION OF THIS FUNCTION
        #
        # I will change that message when I think its not going to ruin your wigle
        # account. And when I think it is safe, I will remove the "return"
        
        if not self.options.get('IwantToLoseMyWigleAccount', False):
            return  # return and don't make requests

        """
        Go through AP list and perform wigle lookup on unknowns
        """
        # These are my plans for making it play nice with the WIGLE API
        # completed ones will be marked with an X
        # - keep under the unknown request limit
        # - pace requests instead of doing bursts
        # - 
        #
        # [X] one lookup per epoch, to slow it down
        # [ ] sort by rssi, to look for likely-closest net first
        # [ ] mark "not in WIGLEs" to not be checked again for a while
        #     - timestamp
        #     - re-checks at lower priority than new nets
        #
        # 
        #
        
        if 'wigle_api_key' not in self.options:
            return

        if time.time() < self._wigle_timeout:
            return
        
        # look for /root/handshakes/AP_MAC.gps.json file for each, and skip if present
        for ap in sorted(self._aps, key=operator.attrgetter( 'rssi')):
            logging.info("Looking up %s" % repr(ap))
            basename = self.getFileBasename(ap['hostname'], ap['mac'])
            fname = "/root/handshakes/%s.gps.json" % basename

            if basename in self._known_aps: # already looked this one up
                logging.info("Already know %s" % fname)
                pass
            elif os.path.isfile(fname):
                logging.info("Already have %s" % fname)
                pass
            else: # look up in wigle
                try:
                    if 'mac' in ap:
                        url = "https://api.wigle.net/api/v2/network/detail?netid=%s" % urllib.parse.quote(ap['mac'])
                        logging.info("Looking up %s: %s" % (ap['hostname'], url))
                        request = urllib.request.Request(url)
                        request.add_header('Authorization', 'Basic %s' % self.options['wigle_api_key'])
                        response = urllib.request.urlopen(request)
                        ap_data = json.loads(response.read())
                        logging.debug("Got from wigle: %s" % json.dumps(ap_data, indent=4))
                        location = ap_data['results'][0]['locationData']
                        if 'latitude' in location and 'longitude' in location:
                            # replicate the lat/long fields so webgpsmap can find them
                            location['Latitude'] = location.get('latitude')
                            location['Longitude'] = location.get('longitude')
                            with open(fname, "w") as f:
                                f.write(json.dumps(location))
                            logging.info("Saved %s" % json.dumps(location))
                except urllib.error.HTTPError as e:
                    logging.error(e)
                    self._wigle_timeout = time.time() + 60 * 60 * 12 # pause requests for 12 hours
                    logging.error("Pausing Wigle requests until %d" % self._wigle_timeout)
                    break
                except Exception as e:
                    logging.exception(e)
                    break
        # grab data from wigle if not
        
        
        pass

    # called to setup the ui elements
    def on_ui_setup(self, ui):
        # add custom UI elements
        ui.add_element('pwn-gpsd', LabeledValue(color='Yellow', label='Lat:\nLong:', value='Lat/Long', position=(ui.width() / 2 + 25, 130),
                                           label_font=fonts.Bold, text_font=fonts.Medium))
        self._ui_elements.append('pwn-gpsd')

    # called when the ui is updated
    def on_ui_update(self, ui):
        try:
            if self._current_location:
                ui.set('pwn-gpsd', "%0.4f\n %0.4f\n%d" % (self._current_location['Latitude'], self._current_location['Longitude'], self._current_location.get("Stations", 0)))
        except Exception as e:
            logging.exception(e)

    # called when everything is ready and the main loop is about to start
    def on_ready(self, agent):
        # open socket for gpsd clients
        addr = ("", 0) # all interfaces, any port
        self._socket = socket.create_server(addr)
        if socket.has_dualstack_ipv6():
            s = socket.create_server(addr, family=socket.AF_INET6, dualstack_ipv6=True)
        else:
            s = socket.create_server(addr)

        if s:
            self._server_socket = s
            #agent.run('set gps.device %s:%d' % (self._server_socket.getsockname()[0], self._server_socket.getsockname()[1]))
            #agent.run('gps on')

        # connect to system GPSD, if available
        # parse input from GPSD and forward to clients when significant changes happen
        # like moved more than N meters, or a minimum time between stagnant updates
        # act as a low-pass filter for gpsd. set up as a separate thread and then use
        # locks around the socket writing.  pause wigle estimates when gpsd lock is active
        
        logging.info("PWN_GPS Ready")


    def on_bcap_wifi_ap_new(self, agent, event):
        """
        Add this AP to the current set and updateLocation
        """
        try:
            ap = event['data']
            self._aps.append(event['data'])
            
            basename = self.getFileBasename(ap['hostname'], ap['mac'])
            fname = "/root/handshakes/%s.gps.json" % basename
            if basename in self._known_aps or os.path.isfile(fname):
                self.updateLocation()
        except Exception as e:
            logging.exception(e)
            
    def on_bcap_wifi_ap_lost(self, agent, event):
        try:
            lost_ap = event['data']
            lost_mac = lost_ap['mac'].lower()
            for ap in self._aps:
                if lost_mac == ap['mac'].lower():
                    if not 'hostnanme' in lost_ap:
                        lost_ap['hostname'] = ap['hostname']
                    self._aps.remove(ap)
                    break

            basename = self.getFileBasename(lost_ap['hostname'], lost_ap['mac'])
            fname = "/root/handshakes/%s.gps.json" % basename
            if basename in self._known_aps or os.path.isfile(fname):
                self.updateLocation()
        except Exception as e:
            logging.exception(e)

#
# if run on command line, act as a GPSD proxy
# with minimum interval between updates, and minimum movement between updates
#
if __name__ == "__main__":
#    from pwnagotchi import log
    from pwnagotchi import utils

    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] (%(filename)s:%(lineno)d) %(funcName)s: %(message)s")
    #formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] (%(filename)s:%(lineno)d) %(funcName)s: %(message)s")
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    root = logging.getLogger()
    root.handlers[0].setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    #logger.addHandler(console_handler)

    
    class FakeArgs:
        def __init__(self, debug=False):
            self.debug = debug
    
   # fake_args = FakeArgs()
   # fake_config = {'main':{'log':{'path':"/tmp/magic.out",
   #                               'console':False,
   #                               'path-debug':"/tmp/magic.err",
   #                               'rotation':{'enabled':False}}}}
    #log.setup_logging(fake_args, fake_config)

    try:
    
        opts, args = getopt.getopt(sys.argv[1:], "SUP:s:k:p:m:d:a:q")
    except getopt.GetoptError as err:
        logging.exception(err)
        sys.exit(2)
        
    server = "127.0.0.1:2947"   # GPSD server to connect to
    proxy_port = 7492           # local port for proxy server
    min_period = 10             # minimum seconds between updates
    ll_decimals = 4             # decimal points precision in lat/long for min update change
    alt_min_chg = 1             # meters delta between updates
    shareWPeers = False
    useSharedLoc = False
    pwngridAdvertising = False
    wantPwngrid = False
    sharingPassword = "Friendship"

    def usage():
        print("pwn-gpsd.py [--quiet] [--port PORT] [--server hostname:port] [--min-period MP] [--decimals LL] [--alt-precision AP]\n")
        print("\tPORT = local port for gpsd proxy server, default 7492\n")
        print("\thostname:port = gpsd to proxy, default localhost:2947\n")
        print("\tMP = minimum time between updates in seconds, integer, default 10\n")
        print("\tLL = decimal point precision on Latitude and Longitude. No update until there is a change in that many decimal places. default 4.  Ex: If 4, lat = 37.2654 will not report again until move to 36.2653 or 36.2655, about 11 meters.  If 3, it won't report until 37.266 or 36.264, about 110 meters\n")
        print("\tAP = minimum change in altitude to trigger GPS proxy update, in same units as preferred for display\n")
        print("\npwn-gpsd executed as a program makes a lower-bandwidth proxy for gpsd. It will proxy WATCH requests, pacing the output as defined by the parameters min_period, ll_decimals, alt_min_chg. While WATCH is active, the server will process data from gpsd, and only send it to clients if min_period seconds have passed AND the location has changed by alt_min_chg height since the last update, or by a distance causing a change in the displayed latitude or longitude down to the ll_decimals decimal point.\n")            
    
    keepGoing = -1 # default to forever

    for o,a in opts:
        print("%s -> %s" % (o,a))
        if o in ("-p", "--port"):
            print(" Setting port to %s" % a)
            proxy_port = int(a)
        elif o in ("-s", "--server"):
            server = a
        elif o in ("-k", "--kount"):
            keepGoing = int(a)
        elif o in ("-m", "--min-period"):
            min_period = int(a)
        elif o in ("-d", "--decimals"):
            ll_decimals = int(a)
        elif o in ("-a", "--alt-precision"):
            alt_decimals = int(a)
        elif o in ("-S", "share"):
            shareWPeers = True
            wantPwngrid = True
        elif o in ("-U", "use-shared"):
            useSharedLoc = True
            wantPwngrid = True
        elif o in ("-P", "--password"):
            sharingPassword = a
        elif o in ("-q", "--quiet"):
            quiet = True

    (host, sport) = server.split(":",1)
    gpsd = PWN_GPSD(host, int(sport), watch=True, password=sharingPassword)
    gpsd_socket = gpsd.socket

    # create proxy socket
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind(("", proxy_port))
        server_socket.listen(5)
        
        read_list = [ server_socket, gpsd_socket ]
    except Exception as e:
        logging.exception(e)
        raise

    messages_archive = {}   # keep track of most recent gpsd message of each type
    messages_for = {}       # message queues for sockets

    def queue_message_for(sock, msg):
        logging.info("Queueing to %s: '%s'" % (sock, msg.strip()))
        if not sock in messages_for:
            messages_for[sock] = msg
        else:
            messages_for[sock] += msg

    def send_messages_for(sock):
        ret = 0
        try:
            if sock in messages_for:
                try:
                    (msg, rest) = messages_for[sock].split("\n", 1)
                except Exception as e:
                    # only one left
                    logging.debug(e)
                    msg = messages_for[sock]
                    rest = None
                if msg != "":
                    msg += "\n"
                    logging.info("--> to %s: %s" % (sock, msg))
                    if sock == gpsd_socket:
                        ret = gpsd.write(msg)
                    else:
                        ret = client_streams[sock].write(msg)
                        client_streams[sock].flush()
                if rest:
                    messages_for[sock] = rest
                else:
                    del messages_for[sock]
            return ret
        except Exception as e:
            logging.exception(e)
            return -1
    
    client_sockets = {}
    client_streams = {}

    last_tpv_send = 0

    last_share_check = 0
    
    while keepGoing:
        if keepGoing > 0:
            keepGoing -= 1

        if wantPwngrid and not pwngridAdvertising:
            try:
                logging.info("Starting pwngrid advertising")
                pwngrid.advertise(True)
                pwngridAdvertising = True
            except Exception as e:
                logging.exception(e)
        try:
            write_list = messages_for.keys()
            if len(write_list):
                logging.info("Write list (%d): %s" % (len(write_list), repr(write_list)))
            if len(read_list):
                logging.debug("Read list (%d): %s" % (len(read_list), repr(read_list)))
            readable, writable, errored = select.select(read_list, write_list, [], 1.0)
            logging.debug("Readable: %s" % repr(readable))
            # look up location from pwngrid peers
            if useSharedLoc and time.time() - last_share_check > 30:
                last_share_check = time.time()
                # get list of peers
                peers = pwngrid.peers()

                friend_locs = []
                for p in peers:
                    adv = p.get('advertisement', {})
                    pos = gpsd.decrypt_data(adv.get('snorlax', {}))
                    if pos:
                        logging.debug("Peer %s pos: %s" % (adv.get('name', ""), pos))
                        p_loc = json.loads(pos)
                        if p_loc:
                            logging.debug(p_loc)
                            p_loc['name'] = adv['name']
                            p_loc['identity'] = adv['identity']
                            p_loc['Cached'] = time.time()
                            friend_locs.append(p_loc)

                # average locations for "my location"
                logging.info("Peer derived location:%s" % friend_locs)
                # store in cache
                if len(friend_locs):
                    last_tpv = json.loads(messages_archive.get('TPV', "{}"))
                    new_tpv = friend_locs[0]
                    if new_tpv.get('mode', -1) >= last_tpv.get('mode', 0):
                        # archiving
                        logging.info("Updating cache from %s" % new_tpv)
                        messages_archive['TPV'] = json.dumps(new_tpv)

            for s in readable:
                if s == server_socket:
                    # new client connected
                    client_socket, address = server_socket.accept()
                    client_sockets[client_socket] = PWN_GPSClient(client_socket, address)
                    client_streams[client_socket] = client_socket.makefile(mode="rw")
                    read_list.append(client_socket)
                    if 'VERSION' in messages_archive:
                        logging.info("Sending VERSION: %s" % messages_archive['VERSION'])
                        queue_message_for(client_socket, messages_archive['VERSION']+"\n")
                elif s == gpsd_socket:
                    # process from GPSD
                    try:
                        raw = gpsd.read()
                        if raw:
                            logging.debug("Got %s" % raw.strip())
                            try:
                                data = json.loads(raw)
                                m_class = data.get('class', None)
                            except Exception as e:
                                logging.exception("Bad JSON: '%s'\n%s" % (raw, e))
                            if m_class == 'VERSION':
                                # newly connected, so start the watch
                                logging.info("Sending Watch and Devices requests")
                                queue_message_for(s, '?WATCH={"enable":true, "json":true};\n')
                                #queue_message_for(s, '?DEVICES;\n')
                            elif m_class == 'WATCH':
                                print("WATCH")
                                for k in data.keys():
                                    if k == "class":
                                        continue
                                    if data[k] == True:
                                        print("\t%s" % k)
                            elif m_class == 'DEVICE':
                                print("GPSD> %s" % raw.strip())
                                for cl in client_sockets.keys():
                                    if client_sockets[cl].watch.get('enable', False):
                                        queue_message_for(cl, raw)
                            elif m_class == 'DEVICES':
                                print("GPSD> DEVICES %s %s" % (data['devices'][0]['path'], data['devices'][0]['driver']))
                            elif m_class == 'TPV': # position update
                                last_tpv = json.loads(messages_archive.get("LAST_SENT_" + m_class, "{}"))
                                last_tpv["time"] = data.get("time", "")
                                if last_tpv == data:
                                    # same data, so skip it
                                    pass
                                else:
                                    mode = data.get('mode', -1)
                                    if mode == 1:
                                        logging.debug("Time ept: %s" % (data.get('ept', "--")))
                                    else:
                                        logging.debug("Mode %s: %s" % (mode, repr(data)))

                                    logging.debug("PWNgpsd (%d)> %s" % ( time.time() - last_tpv_send, raw.strip()))

                                    propagate = False # do not pass along unless something changed
                                    if (time.time()-last_tpv_send > 30):
                                        # minimum update interval
                                        logging.info("Triggering minimum update rate")
                                        propagate = True

                                    if mode > 1:
                                        # have some position
                                        for k,v in {'lat':"%0.4f", 'lon':"%0.4f"}.items():
                                            #if not propagate and abs(data.get(k, last_tpv.get(k, 0)) - last_tpv.get(k, 0)) > v:
                                            new = v % float(data.get(k, 0))
                                            old = v % float(last_tpv.get(k,0))
                                            if not propagate and (new != old):
                                                # enough to change the needle and minimal time
                                                if (time.time()-last_tpv_send > 10):
                                                    logging.info("Update for %s (%0.4f) %0.4f, %0.4f, %0.2f" % (k,
                                                                                                                   last_tpv.get(k, 0),
                                                                                                                   data.get('lat', 0),
                                                                                                                   data.get('lon', 0),
                                                                                                                   data.get('alt', 0)))
                                                    propagate = True
                                                    try:
                                                        if not os.isdir("/etc/pwnagotchi/pwn_gpsd"):
                                                            os.mkdir("/etc/pwnagotchi/pwn_gpsd")
                                                        with open("/etc/pwnagotchi/pwn_gpsd/current.txt", "w") as f:
                                                            f.write(raw)
                                                        now = datetime.now()
                                                        fname = now.strftime("/etc/pwnagotchi/pwn_gpsd/pwntrack_%Y%m%d.txt")
                                                        if not os.isdir(os.dirname(fname)):
                                                            os.mkdir(os.dirname(fname)))
                                                        with open("/etc/pwnagotchi/pwn_gpsd_current_track.txt", "a+") as f:
                                                            f.write(raw)
                                                    except Exception as e:
                                                        logging.exception("Saving current location: %s" % e)

                                    if propagate:
                                        last_tpv_send = time.time()
                                        last = json.loads(messages_archive.get(m_class, "{}"))
                                        messages_archive["LAST_SENT_%s" % m_class] = raw
                                            
                                        # share with proxy clients
                                        for cl in client_sockets.keys():
                                            if client_sockets[cl].watch.get('enable', False):
                                                if last.get('identity'):
                                                    logging.info("Keeping remote loc")
                                                    queue_message_for(cl, json.dumps(last))
                                                else:
                                                    queue_message_for(cl, raw)
                                        # update peering information
                                        if shareWPeers:
                                            advert = pwngrid.get_advertisement_data()
                                            advert['snorlax'] = gpsd.encrypt_data(raw)
                                            try:
                                                pwngrid.set_advertisement_data(advert)
                                            except Exception as e:
                                                logging.exception(e)
                                                pwngridAdvertising = False
                                        
                            elif m_class == 'SKY': # sats
                                nsats = data.get('nSat', 0)
                                for sat in data.get('satellites', []):
                                    logging.debug ("\t%s\t%0.0f\t%0.0f\t%s" % (sat.get('PRN', 0),
                                                                       sat.get('el', 0),
                                                                       sat.get('az', 0),
                                                                       "+" if sat.get('used', False) else ""))
                                # don't send every time
                                if random.random() > 0.75:
                                    logging.debug ("%d satellites visible:" % nsats)
                                    for cl in client_sockets.keys():
                                        if client_sockets[cl].watch.get('enable', False):
                                            queue_message_for(cl, raw)
                            else:
                                logging.info("Unknown message type: %s" % raw.strip())
                            # store latest message of each type
                            #raw['PWNCached'] = time.time()    # mark when cached
                            if m_class == 'TPV':
                                last = json.loads(messages_archive.get(m_class, "{}"))
                                if last.get('identity'):
                                    logging.info("Keeping remote loc")
                                else:
                                    messages_archive[m_class] = raw
                            else:
                                messages_archive[m_class] = raw
                    except Exception as e:
                        logging.exception(e)
                else:
                    # process input from client
                    try:
                        logging.info("process from client")
                        raw = client_sockets[s].read()
                        if raw == "":
                            logging.exception("\n\n   Closing client %s\n\n" % (s))
                            if s in client_sockets:
                                del client_sockets[s]
                            if s in messages_for:
                                del messages_for[s]
                            read_list.remove(s)
                            s.close()
                        logging.info("Got %s from %s" % (raw, s))
                        if raw.startswith("?"):
                            if '=' in raw[1:]:
                                (cmd, data) = raw[1:].strip().split('=',1)
                            else:
                               (cmd, ignore) = raw[1:].strip().split(';',1)
                               data = "{}"
                            logging.info("Client command: %s" % cmd)
                            if cmd == "WATCH":
                                try:
                                    jdata = json.loads(data.strip().strip(';'))
                                    if jdata.get("enable", False):
                                        logging.info("        Client %s Watch: %s\n\n" % (s, json.dumps(jdata, indent=3)))
                                        client_sockets[s].watch = jdata
                                        for upd in [ "TPV", "SKY" ]:
                                            if upd in messages_archive:
                                                logging.info("Sending %s to %s" % (upd, s))
                                                queue_message_for(s, messages_archive[upd])
                                    else:
                                        client_sockets[s].watch = {}
                                except Exception as e:
                                    logging.exception("JDATA: %s" % e)
                            elif cmd == "DEVICES":
                              try:
                                  if 'DEVICES' in messages_archive:
                                      logging.info("Sending DEVICES %s" % (s))
                                      queue_message_for(s, messages_archive['DEVICES'])
                              except Exception as e:
                                  logging.exception(e)
                                  
                            elif cmd == "POLL":
                              try:
                                  jdata = {'class':"POLL",
                                           'time': datetime.now().strftime("%Y-%m-%dT%H:%m:%sZ"),
                                           'active': 1,
                                           }
                                  logging.info("Archive contains: %s" % ",".join(messages_archive.keys()))
                                  if "TPV" in messages_archive:
                                      jdata['tpv'] = [json.loads(messages_archive['TPV'])]
                                  if "SKY" in messages_archive:
                                      jdata['sky'] = [json.loads(messages_archive['SKY'])]
                                  else:
                                      pass
                                  out = json.dumps(jdata)
                                  logging.info("Sending to %s: '%s'" % (s, out))
                                  queue_message_for(s, out)
                              except Exception as e:
                                  logging.exception(e)
                                  
                            else:
                                logging.info("CMD %s: %s" % (cmd, data))
                                queue_message_for(gpsd_socket, raw)
                    except Exception as e:
                        logging.exception("Closing client %s: %s" % (s,e))
                        if s in client_sockets:
                            del client_sockets[s]
                        if s in messages_for:
                            del messages_for[s]
                        read_list.remove(s)
                        s.close()
                        
            for s in writable:
                try:
                    ret = send_messages_for(s)
                    if ret < 0:
                        logging.info("\n\n\tClosing client %s\n\n" % (s))                        
                        if s == gpsd_socket:
                            raise
                        if s in client_sockets:
                            del client_sockets[s]
                        if s in messages_for:
                            del messages_for[s]
                        if s in read_list:
                            read_list.remove(s)
                        s.close()
                                
                except Exception as e:
                    logging.exception("Writing %s: %s" % (s, e))
                    if s == gpsd_socket:
                        raise
                    if s in client_sockets:
                        del client_sockets[s]
                    if s in messages_for:
                        del messages_for[s]
                    read_list.remove(s)
                    s.close()

        except Exception as e:
            logging.exception(e)

    logging.info("Exiting")
    for s in read_list:
        try:
            s.close()
        except Exception as e:
            logging.exception(e)
        
