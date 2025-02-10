import logging

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import *
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.mesh.peer import Peer

import numpy
import random
import os
from datetime import datetime
from datetime import timedelta
import time
import json
import hashlib
import base64
from urllib.parse import urlparse, unquote
from cryptography.fernet import Fernet
from PIL import Image, ImageDraw, ImageFont


class gpsImage(Widget):
    def __init__(self, position=(219,120,319,220), color='White', *, font=None, password="Friendship", tracks=[]):
        super().__init__(position, color)
        self.xy = position
        self.canvas = None
        self.image = None
        self.color = color
        self.font = font
        self.points = {}
        self.bounds = None
        self.password = password
        self.mylocation = {}
        self.tracks = tracks
        self.trackColors = ["#00ff00", "#00ff80", "#00c0c0", "#40c0c0", "#c0ffee", "#c080c0"]
        self.track_lims = [200,200,-200,-200]
        self.fullscreen = None

        if len(tracks):
            for track in self.tracks:
                for step in track:
                    lat = step.get('lat')
                    lon = step.get('lon')
                    if lat < self.track_lims[1]:
                        self.track_lims[1] = lat
                    if lat > self.track_lims[3]:
                        self.track_lims[3] = lat
                    if lon < self.track_lims[0]:
                        self.track_lims[0] = lon
                    if lon > self.track_lims[2]:
                        self.track_lims[2] = lon
                logging.info("%s steps in bbox %s" % (len(track), self.track_lims))

        logging.info("%s steps in bbox %s" % (len(self.tracks), self.track_lims))
        self.state = True # i think this makes it touchable

        if not self.font:
            self.font = ImageFont.truetype("DejaVuSansMono", 12)
        
    def generate_key(self, password=None):
        """Generate a Fernet key from a password"""
        if not password:
            password = self.password
        ekey = hashlib.sha256(password.encode()).digest()
        return base64.urlsafe_b64encode(ekey)

    def decrypt_data(self, encrypted_message, default=None):
        """Decrypts a message with a password."""
        if encrypted_message:
            f = Fernet(self.generate_key())
            try:
                decrypted_message = f.decrypt(encrypted_message.encode()).decode()
            except Exception as e:
                f = Fernet(self.generate_key(password="Friendship"))
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
        minx = self.track_lims[0]
        miny = self.track_lims[1]
        maxx = self.track_lims[2]
        maxy = self.track_lims[3]

        # get my location
        try:
            if os.path.isfile("/etc/pwnagotchi/pwn_gpsd/current.txt"):
                with open("/etc/pwnagotchi/pwn_gpsd/current.txt", 'r') as f:
                    tpv = json.load(f)
                    self.mylocation = tpv
                    if 'lat' in tpv:
                        logging.debug("Me: %s, %s" % (tpv.get('lat'), tpv.get('lon')))
                        points["me"] = tpv
                        if tpv['lon'] < minx:
                            minx = tpv['lon']
                        if tpv['lon'] > maxx:
                            maxx = tpv['lon']
                        if tpv['lat'] < miny:
                            miny = tpv['lat']
                        if tpv['lat'] > maxy:
                            maxy = tpv['lat']
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
                    x = tpv.get('lon', minx)
                    y = tpv.get('lat', miny)
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
            addx = (maxx - minx)*0.1
            addy = (maxy - miny)*0.1
            self.bounds = (minx-addx, miny-addy, maxx+addx, maxy+addy)
        self.image = None

    def toggleFullscreen(self):
        if not self.canvas:
            return False

        self.image = None
        if self.fullscreen:
            self.xy = self.fullscreen
            self.fullscreen = None
            return False
        else:
            self.fullscreen = self.xy
            self.xy = (4, 4, self.canvas.width-4, self.canvas.height-4)
            return True

    def draw(self, canvas, drawer):
        if not self.bounds:
            return

        logging.debug("Bounds: %s Track bounds %s" % (repr(self.bounds), self.track_lims))
        self.canvas = canvas

        if not self.image:
          try:
            w = int(abs(self.xy[0] - self.xy[2]))
            h = int(abs(self.xy[1] - self.xy[3]))
            logging.debug("Width %s, height %s" % (w,h))
            im = Image.new('RGBA', (w,h), self.color)
            dr = ImageDraw.Draw(im)
            dr.fontmode = '1'
            dr.rectangle((0,0,w-1,h-1), fill=None, outline='#c0c0c0')

            # midpoint longitude and latitude
            mex = (self.bounds[2] + self.bounds[0])/2
            mey = (self.bounds[3] + self.bounds[1])/2
            scalex = w/(self.bounds[2] - self.bounds[0])
            scaley = (h)/(self.bounds[3] - self.bounds[1])

            logging.debug("Scale is %s or %s" % (scalex, scaley))

            # choose smaller scale
            if scalex > scaley:
                scalex = scaley
            else:
                scaley = scalex
            logging.info("Bounds: %s Track bounds %s" % (repr(self.bounds), self.track_lims))

            if self.fullscreen:
                scaley *= 0.9
                scalex *= 0.9
            else:
                scaley *= 0.9
                scalex *= 0.9

            
            logging.debug("Track %s, %s %s, %s" % (w, h,self.bounds[2]-self.bounds[0], self.bounds[3]-self.bounds[1]))
            logging.debug("Midpoint: %s, %s" % (mex, mey))
            # draw tracks first
            for i in range(len(self.tracks)-1, -1, -1):
                if self.trackColors[i]:
                    logging.info("Track %d, %s %d" % (i, self.trackColors[i], len(self.tracks[i])))
                    for step in self.tracks[i]:
                        lat = step.get('lat')
                        lon = step.get('lon')
                        x = (step.get('lon') - mex) * scalex + w/2
                        y = (step.get('lat') - mey) * scaley + h/2
                        #logging.info(" Point - %s, %s, %s, %s %s" % (step.get('lat'), step.get('lon'), x, y, self.trackColors[i]))
                        dr.point((x,h-y), fill=self.trackColors[i])

            # then peers
            i = 0
            for name, tpv in self.points.items():
                lat = tpv.get('lat')
                lon = tpv.get('lon')
                x = (tpv.get('lon') - mex) * scalex + w/2
                y = (tpv.get('lat') - mey) * scaley + h/2
                dr.point((x,h-y), fill="black")
                logging.debug("%s: %s, %s == %s, %s of %s, %s" % (name, lat, lon, x,y, w,h))
                yoff = -1 if y > h/2 else 8
                xoff = 1 if x < w/2 else -20
                if name == "me":
                    dr.text((x+1,h-(y+yoff)), name, font=self.font, fill="blue")                    
                elif i % 2:
                    dr.text((x+1,h-(y+yoff)), name, font=self.font, fill="red")
                else:
                    dr.text((x+1,h-(y+yoff)), name, font=self.font, fill="brown")
                i += 1
            self.image = im
        
          except Exception as e:
            logging.exception(e)
        try:
            canvas.paste(self.image, self.xy)
        except ValueError as e:
            logging.error("Resetting image: %s" % e)
            self.image = None
        
class PlotGPS(plugins.Plugin):
    __author__ = 'Sniffleupagus'
    __version__ = '1.0.0'
    __license__ = 'GPL3'
    __description__ = 'An example plugin for pwnagotchi that implements all the available callbacks.'

    def __init__(self):
        self.agent = None
        logging.info("plot_gps plugin created")
        self.password = None
        self.ui_elements = []
        self.tracks = []
        self.gpsImage = None

    # called when http://<host>:<port>/plugins/<plugin>/ is called
    # must return a html page
    # IMPORTANT: If you use "POST"s, add a csrf-token (via csrf_token() and render_template_string)
    def on_webhook(self, path, request):
        try:
            method = request.method
            path = request.path
            query = unquote(request.query_string.decode('utf-8'))
            if "/fullscreen" in path:
                if self.gpsImage:
                    res = "OK - Fullscreen: %s" % self.gpsImage.toggleFullscreen()
                    if self._ui:
                        self._ui.update(force=True)
                    return res, 204


            return "<html><body>Woohoo! %s: %s<p>Request <a href=\"/plugins/plot_gps/fullscreen\">Toggle FullScreen</a></body></html>" % (path, query)
        except Exception as e:
            logging.exception(e)
            return "<html><body>Error! %s</body></html>" % (e)

    # called when the plugin is loaded
    def on_loaded(self):
        self.password = self.options.get('password', 'Friendship')
        pass

    # called before the plugin is unloaded
    def on_unload(self, ui):
        with ui._lock:
            for el in self.ui_elements:
                try:
                    ui.remove_element(el)
                except Exception as e:
                    logging.exception(e)
        ui.update(force=True)
        logging.info("plot_gps out")

    # called when there's internet connectivity
    def on_internet_available(self, agent):
        pass

    # called to setup the ui elements
    def on_ui_setup(self, ui):
      try:
        # add custom UI elements
        self._ui = ui

        now = datetime.now()

        for i in range(6):
            fname = (now - timedelta(days=i)).strftime("/etc/pwnagotchi/pwn_gpsd/pwntrack_%Y%m%d.txt")
            logging.info("Loading day %d, %s" % (i,fname))
            self.tracks.append([])
            if os.path.isfile(fname):
                with open(fname) as f:
                    lines = [line.rstrip() for line in f]
                for l in lines:
                    try:
                        l = l.strip(",")
                        tpv = json.loads(l)
                        self.tracks[i].append(tpv)
                    except Exception as e:
                        logging.exception("%s: %s" % (l, e))
                logging.info("Read track %d with %s steps" % (i, len(self.tracks[i])))

        self.gpsImage = gpsImage(password=self.password, tracks=self.tracks)
        
        self.fields = self.options.get('fields', ['fix','lat','lon','alt','speed'])
        base_pos = self.options.get('pos', [0,55])
        with ui._lock:
            for f in self.fields:
                fname = "plot_gps_" + f
                pos = (base_pos[0], base_pos[1])
                label = f
                if f == 'fix':
                    label = ">O<"
                ui.add_element(fname, LabeledValue(color="black", label=label, value='---.----',
                                                   position=pos,
                                                   label_font=fonts.BoldSmall, text_font=fonts.Small),
                               )
                self.ui_elements.append(fname)
                base_pos[1] += 10
            ui.add_element('plot_gps', self.gpsImage)
            self.ui_elements.append('plot_gps')
      except Exception as e:
          logging.exception(e)


    # called when the ui is updated
    def on_ui_update(self, ui):
        # update those elements
        if not self.agent or not self.gpsImage:
            return
        try:
            if self.gpsImage.mylocation:
                self.fields = self.options.get('fields', ['fix','lat','lon','alt','spd'])
                loc = self.gpsImage.mylocation
                mode = loc.get('mode', 0)
                if mode == 0:
                    fix = '-'
                elif mode == 1:
                    fix = 'T'
                else:
                    fix = "%sD" % mode
                ui.set("plot_gps_fix", fix)

                for f in ['lat', 'lon']:
                    fname = "plot_gps_%s" % f
                    fval = loc.get(f, None)
                    if fval:
                        ui.set(fname, "%9.4f" % fval)

                alt = loc.get('alt', None)
                if alt:
                    ui.set('plot_gps_alt', "%6.2f" % alt)

                speed = loc.get('speed', None)
                if speed:
                    speed = speed * 2.237  # miles per hour. use 3.6 for kph
                    ui.set('plot_gps_speed', "%6.2f" % speed)
                else:
                    ui.set('plot_gps_speed', "---")
            else:
                logging.info("No location yet: %s" % repr(self.gpsImage.mylocation))
                self.gpsImage.processPeers(self.agent._peers)

        except Exception as e:
            logging.exception(e)

    def on_touch_press(self, ts, ui, ui_element, touch_data):
        logging.info("[PLOT] Touch press: %s, %s" % (touch_data, ui_element));

    def on_touch_release(self, ts, ui, ui_element, touch_data):
        logging.info("[PLOT] Touch release: %s, %s" % (touch_data, ui_element));
        if ui_element == "plot_gps":
            self.gpsImage.toggleFullscreen()
            ui.update(force=True)

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
        if True:
            try:
                if self.gpsImage.mylocation:
                    tpv = self.gpsImage.mylocation
                else:
                    if os.path.isfile("/etc/pwnagotchi/pwn_gpsd/current.txt"):
                        with open("/etc/pwnagotchi/pwn_gpsd/current.txt", 'r') as f:
                            tpv = json.load(f)
                if 'lat' in tpv:
                    gps_filename = filename.replace(".pcap", ".pgps.json")
                    logging.info(f"saving GPS to {gps_filename} ({tpv})")
                    with open(gps_filename, "w+t") as fp:
                        json.dump(tpv, fp)
                else:
                    logging.info("not saving GPS. Couldn't find location.")
            except Exception as err:
                logging.exception("[pwn-gpsd handshake] %s" % repr(err))

    # called when an epoch is over (where an epoch is a single loop of the main algorithm)
    def on_epoch(self, agent, epoch, epoch_data):
        if self.gpsImage:

            now = datetime.now()
            fname = now.strftime("/etc/pwnagotchi/pwn_gpsd/pwntrack_%Y%m%d.txt")
            logging.info("Loading %s" % (fname))
            if os.path.isfile(fname):
                with open(fname) as f:
                    lines = [line.rstrip() for line in f]
                for l in lines:
                    try:
                        l = l.strip(",")
                        tpv = json.loads(l)
                        self.tracks[0].append(tpv)
                    except Exception as e:
                        logging.exception("%s: %s" % (l, e))
                logging.info("Read track %d with %s steps" % (i, len(self.tracks[0])))

            
            self.gpsImage.processPeers(self.agent._peers)

    # called when a new peer is detected
    def on_peer_detected(self, agent, peer):
        if self.gpsImage:
            self.gpsImage.processPeers(agent._peers)

    def on_peer_updated(self, agent, peer):
        logging.info("Peer updated %s" % peer)
        if self.gpsImage:
            self.gpsImage.processPeers(agent._peers)

    # called when a known peer is lost
    def on_peer_lost(self, agent, peer):
        if self.gpsImage:
            self.gpsImage.processPeers(agent._peers)

