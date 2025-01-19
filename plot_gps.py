import logging

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import *
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.mesh.peer import Peer

import random
import os
import json
import hashlib
import base64
from cryptography.fernet import Fernet
from PIL import Image, ImageDraw, ImageFont


class gpsImage(Widget):
    def __init__(self, position=(219,120,319,220), color='White', *, font=None):
        super().__init__(position, color)
        self.xy = position
        self.color = color
        self.font = font
        self.points = {}
        self.bounds = None

        if not self.font:
            self.font = ImageFont.truetype("Pillow/Tests/fonts/FreeMono.ttf", 12)
        
    def generate_key(self, password="Friendship"):
        """Generate a Fernet key from a password"""
        if password:
            ekey = hashlib.sha256(password.encode()).digest()
            return base64.urlsafe_b64encode(ekey)
        else:
            return None

    def decrypt_data(self, encrypted_message, default=None):
        """Decrypts a message with a password."""
        if encrypted_message:
            f = Fernet(self.generate_key())
            decrypted_message = f.decrypt(encrypted_message.encode()).decode()
            try:
                return json.loads(decrypted_message)
            except Exception as e:
                logging.error("Got text not json: %s, %s" % (decrypted_message, e))
                return decrypted_message
        else:
            return default

    def processPeers(self, peers):
        points = {}
        minx = None
        maxx = None
        miny = None
        maxy = None

        # get my location
        try:
            if os.path.isfile("/etc/pwnagotchi/pwn_gpsd/current.txt"):
                logging.info("Loading current loc")
                with open("/etc/pwnagotchi/pwn_gpsd/current.txt", 'r') as f:
                    tpv = json.load(f)
                    if 'lat' in tpv:
                        points["me"] = tpv
                        minx = tpv.get('lat')
                        maxx = tpv.get('lat')
                        miny = tpv.get('lon')
                        maxy = tpv.get('lon')
            else:
                logging.info("Nope")
        except exception as e:
            logging.exception(e)

        
        logging.debug(peers)
        for id,p in peers.items():
            #logging.info("Peer: %s" % p)
            adv = p.adv   # wtf
            if 'snorlax' in adv:
                tpv = json.loads(self.decrypt_data(adv.get('snorlax', {})))
                logging.debug("%s: %s" % (p.name(), tpv))
                if 'lat' in tpv:
                    logging.info("%s -> %s, %s" % (p.name(), tpv.get('lat', random.randint(0,100)), tpv.get('lon', random.randint(0,100))))
                    points[p.name()] = tpv
                    x = tpv.get('lat', minx)
                    y = tpv.get('lon', miny)
                    if not minx or x < minx:
                        minx = x
                    if not miny or y < miny:
                        miny = y
                    if not maxx or x >maxx:
                        maxx = x
                    if not maxy or y > maxy:
                        maxy = y

        if minx != None:
            self.points = points
            # catch zero
            if minx == maxx:
                minx -= 1
                maxx += 1
            if miny == maxy:
                miny -= 1
                maxy += 1
            addx = (maxx - minx)*0.2
            addy = (maxy - miny)*0.2
            self.bounds = (minx-addx, miny-addy, maxx+addx, maxy+addy)

    def draw(self, canvas, drawer):
        if not self.bounds:
            return
        logging.debug("Bounds: %s" % repr(self.bounds))
        
        try:
            w = int(abs(self.xy[0] - self.xy[2]))
            h = int(abs(self.xy[1] - self.xy[3]))
            logging.debug("Width %s, height %s" % (w,h))
            im = Image.new('RGBA', (w,h), self.color)
            dr = ImageDraw.Draw(im)
            dr.fontmode = '1'

            # me in the middle
            if "meeeeee" in self.points:
                logging.info("Me in the middle")
                mex = self.points['me'].get('lat')
                mey = self.points['me'].get('lon')
                if abs(self.bounds[2] - mex) > abs(self.bounds[0] - mex):
                    scalex = w/abs(self.bounds[2] - mex)
                else:
                    scalex = w/abs(self.bounds[0] - mex)
                if abs(self.bounds[3] - mey) > abs(self.bounds[1] - mey):
                    scaley = w/abs(self.bounds[3] - mey)
                else:
                    scaley = w/abs(self.bounds[1] - mey)
            else:
                scalex = w/(self.bounds[2] - self.bounds[0])
                scaley = h/(self.bounds[3] - self.bounds[1])
            
            i = 0
            for name, tpv in self.points.items():
                x = (tpv.get('lat') - self.bounds[0]) * scalex
                y = h - (tpv.get('lon') - self.bounds[1]) * scaley
                dr.point((x,h-y), fill="black")
                if name == "me":
                    dr.text((x+1,h-(y-1)), name, font=self.font, fill="blue")                    
                elif i % 2:
                    dr.text((x+1,h-(y-1)), name, font=self.font, fill="red")
                else:
                    dr.text((x+1,h-(y+9)), name, font=self.font, fill="red")
                i += 1
                canvas.paste(im, self.xy)
        except Exception as e:
            logging.exception(e)
        
class PlotGPS(plugins.Plugin):
    __author__ = 'Sniffleupagus'
    __version__ = '1.0.0'
    __license__ = 'GPL3'
    __description__ = 'An example plugin for pwnagotchi that implements all the available callbacks.'

    def __init__(self):
        self.agent = None
        logging.info("plot_gps plugin created")

    # called when http://<host>:<port>/plugins/<plugin>/ is called
    # must return a html page
    # IMPORTANT: If you use "POST"s, add a csrf-token (via csrf_token() and render_template_string)
    def on_webhook(self, path, request):
        pass

    # called when the plugin is loaded
    def on_loaded(self):
        pass

    # called before the plugin is unloaded
    def on_unload(self, ui):
        try:
            ui.remove_element('peer_gps')
        except Exception as e:
            logging.exception(e)

    # called when there's internet connectivity
    def on_internet_available(self, agent):
        pass

    # called to setup the ui elements
    def on_ui_setup(self, ui):
        # add custom UI elements
        self._ui = ui
        self.gpsImage = gpsImage()
        
        ui.add_element('peer_gps', self.gpsImage) 

    # called when the ui is updated
    def on_ui_update(self, ui):
        # update those elements
        if not self.agent or not self.gpsImage:
            return
        try:
            self.gpsImage.processPeers(self.agent._peers)
        except Exception as e:
            logging.exception(e)

    # called when the hardware display setup is done, display is an hardware specific object
    def on_display_setup(self, display):
        pass

    # called when everything is ready and the main loop is about to start
    def on_ready(self, agent):
        self.agent = agent
        logging.info("READY")
        # you can run custom bettercap commands if you want
        #   agent.run('ble.recon on')
        # or set a custom state
        #   agent.set_bored()


    # called when the agent refreshed its access points list
    def on_wifi_update(self, agent, access_points):
        pass

    # called when the agent refreshed an unfiltered access point list
    # this list contains all access points that were detected BEFORE filtering
    def on_unfiltered_ap_list(self, agent, access_points):
        pass

    # called when the agent is sending an association frame
    def on_association(self, agent, access_point):
        pass

    # called when the agent is deauthenticating a client station from an AP
    def on_deauthentication(self, agent, access_point, client_station):
        pass

    # callend when the agent is tuning on a specific channel
    def on_channel_hop(self, agent, channel):
        pass

    # called when a new handshake is captured, access_point and client_station are json objects
    # if the agent could match the BSSIDs to the current list, otherwise they are just the strings of the BSSIDs
    def on_handshake(self, agent, filename, access_point, client_station):
        if self.running:
            try:
                if os.path.isfile("/etc/pwnagotchi/pwn_gpsd/current.txt"):
                    logging.info("Loading current loc")
                    with open("/etc/pwnagotchi/pwn_gpsd/current.txt", 'r') as f:
                        tpv = json.load(f)
                        if 'lat' in tpv:
                            gps_filename = filename.replace(".pcap", ".gps.json")
                            logging.info(f"saving GPS to {gps_filename} ({tpv})")
                            with open(gps_filename, "w+t") as fp:
                                json.dump(tpv, fp)
                else:
                    logging.info("not saving GPS. Couldn't find location.")
            except Exception as err:
                logging.warning("[gps_more handshake] %s" % repr(err))

    # called when an epoch is over (where an epoch is a single loop of the main algorithm)
    def on_epoch(self, agent, epoch, epoch_data):
        pass

    # called when a new peer is detected
    def on_peer_detected(self, agent, peer):
        pass

    # called when a known peer is lost
    def on_peer_lost(self, agent, peer):
        pass
