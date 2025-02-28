import logging

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import *
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.mesh.peer import Peer

import os
import glob
from datetime import datetime,timedelta
import time
import json
import random
import hashlib
import base64
from urllib.parse import urlparse,unquote
from cryptography.fernet import Fernet

# gpio for buttons to change view
import RPi.GPIO as GPIO

from PIL import Image, ImageDraw, ImageFont

ADV_FIELD='snorlax'

def checkBounds(overall, new):
    if not new:
        return overall

    if not overall:
        overall = [180,90,-180,-90]

    ret = overall
    if isinstance(new, list):
        if len(new) == 2:
            ret[0] = new[0] if new[0] < overall[0] else overall[0]
            ret[1] = new[1] if new[1] < overall[1] else overall[1]
            ret[2] = new[0] if new[0] > overall[2] else overall[2]
            ret[3] = new[1] if new[1] > overall[3] else overall[3]
        elif len(new) == 4:
            ret[0] = new[0] if new[0] < overall[0] else overall[0]
            ret[1] = new[1] if new[1] < overall[1] else overall[1]
            ret[2] = new[2] if new[2] > overall[2] else overall[2]
            ret[3] = new[3] if new[3] > overall[3] else overall[3]
    elif isinstance(new, dict):
        if 'lat' in new and 'lon' in new:
            try:
                ret[0] = new['lon'] if new['lon'] < overall[0] else overall[0]
                ret[1] = new['lat'] if new['lat'] < overall[1] else overall[1]
                ret[2] = new['lon'] if new['lon'] > overall[2] else overall[2]
                ret[3] = new['lat'] if new['lat'] > overall[3] else overall[3]
            except Exception as e:
                logging.info("overall: %s, new: %s: %s" % (overall,new,e))
        else:
            logging.debug("No lat or lon. skipping: %s" % new)
    else:
        logging.info("Unable to process: Overall %d elements. New %d elements:  %s" % (len(overall), len(new), repr(new)))

    return ret

def boxesOverlap(a, b):
    try:
        if not b:
            logging.exception("None type given")
            return False
        
        if len(b) == 2: # b is a point
            if (a[0] <= b[0] and a[2] >= b[0]) and (a[1] <= b[1] and a[3] >= b[1]):
                return True
            else:
                return False
            
        elif len(b) == 4: # b is a box
            if a[0] > b[2] or b[0] > a[2]:
                # boxes are completely to left or right
                return False

            if a[1] > b[3] or b[1] > a[3]:
                # above or below the other
                return False
        else:
            logging.warn("Expected box or point: %s" % (b))
        return True
    except Exception as e:
        logging.exception(e)
        return False

class gpsTrack:
    name = None
    filename = None
    mtime = 0
    points = None
    bounds = None
    visible = True
    zoomToFit = False
    
    def __init__(self, name, filename=None, visible=True, zoomToFit=False):
        self.name = name
        self.visible = visible
        self.zoomToFit = zoomToFit
        self.gpio = None
        self.points = []

        if filename:
            self.loadFromFile(filename)

    def addPoint(self, tpv):
        if 'lat' in tpv and 'lon' in tpv:
            if not self.points:
                self.points = []

            self.points.append(tpv)
            lat = tpv.get('lat')
            lon = tpv.get('lon')
            if not self.bounds:
                self.bounds = [200,200,-200,-200]

            if lon < self.bounds[0]: self.bounds[0] = lon
            if lat < self.bounds[1]: self.bounds[1] = lat
            if lon > self.bounds[2]: self.bounds[2] = lon
            if lat > self.bounds[3]: self.bounds[3] = lat

    def lastPoint(self):
        if not self.points:
            return None
        return self.points[-1]
            
    def loadFromFile(self, filename, ifUpdated=True):
        try:
            if filename and os.path.isfile(filename):
                self.filename = filename
                mtime = os.stat(filename).st_mtime
                if ifUpdated == False or mtime > self.mtime:
                    logging.debug("loading %s" % filename)
                    self.mtime = mtime
                    with open(filename) as f:
                        tmp = gpsTrack("temp")
                        lines = [line.rstrip() for line in f]
                        for l in lines:
                            try:
                                l = l.strip(",")
                                l = l.strip('\0')
                                tpv = json.loads(l)
                                tmp.addPoint(tpv)
                                
                            except Exception as e:
                                logging.error("- skip line: %s" % e)
                        logging.info("Loaded %s %d steps within %s" % (os.path.basename(filename), len(tmp.points), tmp.bounds))
                        self.points = tmp.points
                        self.bounds = tmp.bounds
                        del tmp
                    return True
                return False
            else:
                logging.warn("No track file: %s" % (filename))
                return False
        except Exception as e:
            logging.exception(e)
        return False

    def reloadFile(self):
        return self.loadFromFile(self.filename)

class Peer_Map(plugins.Plugin, Widget):
    __author__ = 'Sniffleupagus'
    __version__ = '1.0.5'
    __license__ = 'GPL3'
    __description__ = 'Plot gps tracks on pwnagotchi screen'

    def __init__(self):
        self._agent = None
        self._ui = None
        self.password = "Friendship"
        self.fernet = None
        self.ui_elements = []
        self.me = None
        self.tracks = {}
        self.peers = {}
        self.image = None
        self.value = None
        self.xy = None
        self.t_dir = None
        self.font = None
        self.touch_info = {}
        self.zoom_multiplier = 1
        self.gpio = None
        self.window_size = None

        self.state = True # this makes it touchable in Touch_UI plugin
        
        self.track_colors=['#00ff00', '#ffff00', '#ff00ff', '#00ffff', '#40ff40', '#ff8080', '#c0c0ff', '#40c080', '#80c040', '#80c080', '#800000', '#404080'] # a bunch of colors
        self.peer_colors=['red', 'blue', 'purple', 'orange', 'brown']


    def updateImage(self):
      try:
        if not self._ui:
            return
        w = self.xy[2]-self.xy[0]
        h = self.xy[3]-self.xy[1]
        
        image = Image.new('RGBA', (w,h), self.bgcolor)

        d = ImageDraw.Draw(image)
        d.fontmode = '1'
        d.rectangle((0,0,w-1,h-1), fill=self.bgcolor, outline='#808080')

        # compute lon/lat boundaries
        if self.me and self.me.bounds:
            bounds = self.me.bounds.copy()
            logging.debug("Me: %s" % (self.me.bounds))
        else:
            bounds = [180,90,-180,-90]
        for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible and t.zoomToFit:
                bounds = checkBounds(bounds, t.bounds)
        logging.debug("Track bounds: %s" % (bounds))

        pbounds = [180,90, -180,-90]
        logging.debug("Unpacking peers: %s" % (repr(self.peers)))
        for p,tpv in self.peers.items():
            try:
                pbounds = checkBounds(pbounds, json.loads(tpv))
            except Exception as e:
                logging.exception(e)
        logging.debug("Peer bounds: %s" % (pbounds))
        bounds = checkBounds(bounds, pbounds)

        # go one "tick" bigger around the edges
        bounds[2] += 0.0001
        bounds[0] -= 0.0001
        sw = bounds[2] - bounds[0]
        
        bounds[3] += 0.0001
        bounds[1] -= 0.0001
        sh = bounds[3] - bounds[1]

        logging.debug("Final bounds: %s" % (bounds))

        scale = min(w/sw, h/sh) * self.zoom_multiplier    # pixels per map unit
        midpoint = [(bounds[2]+bounds[0])/2, (bounds[3]+bounds[1])/2]
        if self.zoom_multiplier > 1 and self.me.bounds:
            midpoint = [self.me.bounds[0], self.me.bounds[1]]

        map_bbox = [midpoint[0] - (w/2)/scale, midpoint[1] - (h/2)/scale,
                    midpoint[0] + (w/2)/scale, midpoint[1] + (h/2)/scale]
            
        # draw tracks
        i = 0
        for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible:  #and boxesOverlap( map_bbox, t.bounds):
                # visible and overlaps, so plot it
                logging.debug("Plotting %s %s" % (f, t.bounds))
                lp = None
                color = self.track_colors[i % len(self.track_colors)]
                logging.debug("Scale: %s, %s, map box: %s" % (scale, color, map_bbox))
                for p in t.points:
                    x = (p['lon'] - midpoint[0]) * scale + w/2
                    y = (p['lat'] - midpoint[1]) * scale + h/2
                    #logging.info("Point:(%s %s), (%s %s)" % (p['lon'],x, p['lat'],y))
                    d.point((x, h-y), fill = color)
                i += 1

        # draw peers
        i = 1
        for p, tpv in self.peers.items():
            logging.debug("PEER info tpv: %s, %s" % (type(tpv), tpv))
            if isinstance(tpv, str):
                try:
                    data = json.loads(tpv)
                except Exception as e:
                    logging.exception("Error reading json: %s" % e)
            if 'lat' in data and 'lon' in data:
                x = (data['lon'] - midpoint[0]) * scale + w/2
                y = (data['lat'] - midpoint[1]) * scale + h/2
                pc = self.peer_colors[i % len(self.peer_colors)]
                d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=pc)
                tbox = self.font.getbbox(data.get('name', "XXX"))
                xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
                yoff = int(0 if (y-tbox[3]) > 0 else tbox[3])
                d.text((x+xoff,h-(y+yoff)), data.get('name', "XXX"), fill=pc, font=self.font)
                logging.debug("Plot peer: %s, %s" % (p, tpv))
                i += 1

        # draw me
        if self.me and self.me.bounds:
            logging.debug("Me: %s" % (self.me.bounds))
            x = (self.me.bounds[0] - midpoint[0]) * scale + w/2
            y = (self.me.bounds[1] - midpoint[1]) * scale + h/2
            d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=self.peer_colors[0])
            tbox = self.font.getbbox("me")
            xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
            yoff = int(0 if (y-tbox[3]) > 0 else tbox[3]+2)

            logging.debug("Offset: %s %s = %s" % (xoff, yoff, self.color))
            d.text((x+xoff,h-(y+yoff)), "me", fill=self.color, font=self.font)
        self.image = image
      except Exception as e:
          logging.exception(e)

    def draw(self, canvas, drawer):
        if not self.image:
            self.updateImage()
        if self.image and self.xy:
            try:
                canvas.paste(self.image.convert(canvas.mode), self.xy)
            except Exception as e:
                logging.error(e)
                self.image = None

    def on_ready(self, agent):
        self._agent = agent

    def on_loaded(self):
      try:
        logging.info("peer_map loaded with options %s" % (self.options))
        self.password = self.options.get('password', None)
        if self.password:
            self.fernet = Fernet(self.generateKey())

        if 'track_colors' in self.options:
            self.track_colors = self.options['track_colors']
        if 'peer_colors' in self.options:
            self.peer_colors = self.options['peer_colors']

        now = datetime.now()
        self.t_dir = self.options.get("track_dir", "/etc/pwnagotchi/pwn_gpsd")
        tracks_fname_fmt = self.options.get("track_fname_fmt", "pwntrack_%Y%m%d.txt")
        n = 0
        i = 0
        while i < 30 and n < self.options.get("days", 3):
            fname = (now - timedelta(days=i)).strftime(tracks_fname_fmt)
            logging.debug("Looking for %s" % os.path.join(self.t_dir, fname))
            if os.path.isfile(os.path.join(self.t_dir, fname)):
                t = gpsTrack(fname, os.path.join(self.t_dir, fname), True, True)
                self.tracks[fname] = t
                n += 1
            i += 1

        tracks_fname_fmt = self.options.get("track_fname_fmt", "peertrack_%Y%m%d.txt")
        n = 0
        i = 0
        while i < 30 and n < self.options.get("days", 3):
            fname = (now - timedelta(days=i)).strftime(tracks_fname_fmt)
            logging.debug("Looking for %s" % os.path.join(self.t_dir, fname))
            if os.path.isfile(os.path.join(self.t_dir, fname)):
                t = gpsTrack(fname, os.path.join(self.t_dir, fname), True, True)
                self.tracks[fname] = t
                n += 1
            i += 1

        fname = os.path.join(self.t_dir, "current.txt")
        if os.path.isfile(fname):
            self.me = gpsTrack("current", fname, True, True)
            logging.debug("Read my location: %s" % (self.me.bounds))

        self.gpio = self.options.get("gpio", None)
        if self.gpio:
            try:
                GPIO.setmode(GPIO.BCM)
                for action in ['zoom_in', 'zoom_out']:
                    if action in self.gpio:
                        if action == 'zoom_in':
                            cb = self.zoom_in
                        elif action == 'zoom_out':
                            cb = self.zoom_out
                        else:
                            cb = self.handle_button
                        p = self.gpio[action]
                        logging.info("Setting up %s -> %s" % (action, p))
                        GPIO.setup(p, GPIO.IN, GPIO.PUD_UP)
                        logging.info("Setting event %s -> %s" % (action, p))
                        GPIO.add_event_detect(p, GPIO.FALLING, callback=cb,
                                   bouncetime=800)
                        logging.info("Set up %s on pin %d" % (action, p))
            except Exception as gpio_e:
                logging.exception("Loading GPIO: %s" % gpio_e)
      except Exception as e:
          logging.exception(e)

    def zoom_in(self, channel):
      try:
        if not self._ui:
            return
    
        if self.window_size:
            self.zoom_multiplier *= 2
        else:
            self.window_size = self.xy.copy()
            border = self.options.get('border', 5)
            self.xy = (border, border, self._ui.width()-border, self._ui.height()-border)
        self.image = None
      except Exception as e:
          logging.exception("Zoom in: %s: %s" % (channel, e))

    def zoom_out(self, channel):
      try:
        if not self._ui:
            return

        if self.zoom_multiplier >1.5:
            self.zoom_multiplier /= 2
        elif self.window_size:
            self.xy = self.window_size.copy()
            self.window_size = None
        self.image = None
      except Exception as e:
          logging.exception("Zoom in: %s: %s" % (channel, e))

    def handle_button(self, channel):
        """GPIO button handler. Use channel as index into self.gpio to get config.toml entry for that button"""
        if self.gpio and channel in self.gpio:
            logging.info("Clicked %s: %s" % (channel, self.gpio[channel]))
        else:
            logging.info("Unexpected click: %s, %s" % (channel, self.gpio))

    def generateKey(self):
        if not self.password:
            return None
        else:
            ekey = hashlib.sha256(self.password.encode()).digest()
            return base64.urlsafe_b64encode(ekey)

    # decrypt a message to json object or text, or default value in case failure
    def decrypt_data(self, encrypted_message, default=None):
        """Decrypts a message with a password."""
        if encrypted_message and self.fernet:
            try:
                decrypted_message = self.fernet.decrypt(encrypted_message.encode()).decode()
                try:
                    return json.loads(decrypted_message)
                except Exception as e:
                    logging.warn("Not JSON: %s, %s" % (e, decrypted_message))
                    return decrypted_message
            except Exception as e2:
                logging.error("Decrypt failed: %s" % (e2))
                return default
        else:
            return default

    def current_touch_status(self):
        return self.touch_info.get('status', {'pressed':False, 'last_press':None})

    def on_touch_press(self, ts, ui, ui_element, touch_data):
        logging.debug("Touch press: %s, %s" % (touch_data, ui_element));

    def on_touch_release(self, ts, ui, ui_element, touch_data):
        logging.info("Touch press: %s, %s" % (touch_data, ui_element));
        if not self.window_size:
            self.zoom_in("touch")
        elif touch_data['point'][0] > ui.width()/2:
            self.zoom_in("touch")
        else:
            self.zoom_out("touch")

        ui.set("peer_map", time.time())

    def on_unload(self, ui):
        with ui._lock:
            for el in self.ui_elements:
                try:
                    logging.info("Removing %s" % el)
                    ui.remove_element(el)
                except Exception as e:
                    logging.error("Unable to remove %s: %s" % (el, e))
        if self.gpio:
            logging.info("GPIO: %s" % (self.gpio))
            for action,pin in self.gpio.items():
                try:
                    logging.info("removing event from %s" % (pin))
                    GPIO.remove_event_detect(pin)
                    GPIO.cleanup(pin)
                except Exception as e:
                    logging.exception("GPIO cleanup %s: %s" % (pin, e))


    def on_ui_setup(self, ui):
        self._ui = ui
        try:
            self.xy = self.options.get("pos", [100,30,200,100])
            self.color = self.options.get("color", "white")
            self.bgcolor = self.options.get("bgcolor", "black")
            self.font = ImageFont.truetype(self.options.get('font', "DejaVuSansMono"),
                                           self.options.get('font_size', 10))

            with ui._lock:
                ui.add_element('peer_map', self)
                self.ui_elements.append('peer_map')
                base_pos = self.options.get('pos', [0,55])
                for field in self.options.get('fields', ['fix', 'lon', 'lat', 'alt', 'speed']):
                    fname = "pm_%s" % field
                    pos = (base_pos[0], base_pos[1])
                    ui.add_element(fname, LabeledValue(color="black", label=field, value='---.----',
                                                       position=pos,
                                                       label_font=fonts.BoldSmall, text_font=fonts.Small),
                               )
                    self.ui_elements.append(fname)
                    base_pos[1] += 10
        except Exception as e:
            logging.exception(e)

    def update_peers(self):
      try:
        ret = False
        agent = self._agent
        if not agent:
            return ret
        for id, p in agent._peers.items():
            adv = p.adv
            if ADV_FIELD in adv:
                logging.debug("Decrypting %s: %s" % (adv.get('name','unknown'), adv))
                tpv = self.decrypt_data(adv.get(ADV_FIELD))
                if tpv and tpv != self.peers.get(id, ""):
                    if isinstance(tpv, str):
                        try:
                            data = json.loads(tpv)
                        except Exception as e:
                            logging.info("Error reading json: %s" % e)
                    if 'lat' in tpv and 'lon' in tpv:
                        logging.debug("Saving PEER %s: %s" % (p.adv.get('name', None), data))
                        data['name'] = p.adv.get('name', "peer")
                        self.peers[id] = json.dumps(data)
                        ret = True
        return ret
      except Exception as e:
          logging.exception(e)
          return ret

    def on_ui_update(self, ui):
        redrawImage = False
        bounds = [180,90,-180,-90]

        # check tracks
        for f in sorted(self.tracks):
            logging.debug("Checking %s" % f)
            t = self.tracks[f]
            if t.visible and t.reloadFile():
                logging.info("%s CHANGED" % f)
                redrawImage = True
        
        # check peers
        if self.update_peers():
            redrawImage = True

        # check me
        if self.me and self.me.reloadFile():
            logging.debug("Me updated: %s" % (self.me.bounds))
            redrawImage = True
            tpv = self.me.lastPoint()
            fields = self.options.get('fields', ['fix', 'lon', 'lat', 'alt', 'speed'])
            units = self.options.get('units', 'metric')
            if 'fix' in fields:
                mode = tpv.get('mode', 0)
                if mode == 0:
                    fix = '-'
                elif mode == 1:
                    fix = 'T'
                else:
                    fix = "%sD" % mode

                if 'undivided_count' in tpv:
                    fix += "-%d" % (int(100 - (tpv['undivided_count'][1]/tpv['undivided_count'][0])/2))

                ui.set('pm_fix', fix)

            for f in ['lon', 'lat']:
                if f in fields:
                    fname = "pm_%s" % f
                    fval = tpv.get(f, None)
                    if fval:
                        ui.set(fname, "%9.4f" % fval)

            if 'alt' in fields:
                alt = tpv.get('alt', tpv.get('altMSL', None))
                if alt:
                    if units in ['feet', 'imperial']:
                        alt *= 3.28084
                    ui.set('pm_alt', "%6.2f" % alt)
                else:
                    ui.set('pm_alt', "---.--")

            if 'speed' in fields:
                speed = tpv.get('speed', None)
                if speed:
                    if units in ['feet', 'imperial']:
                        speed *= 2.237
                    elif units == 'kmh':
                        speed *= 3.6

                    ui.set('pm_speed', "%6.2f" % speed)
                else:
                    ui.set('pm_speed', "---.--")

        if redrawImage:
            self.updateImage()


        
    def on_handshake(self, agent, filename, access_point, client_station):
        try:
            if self.me:
                tpv = self.me.lastPoint()
            elif os.path.isfile("/etc/pwnagotchi/pwn_gpsd/current.txt"):
                with open("/etc/pwnagotchi/pwn_gpsd/current.txt", 'r') as f:
                    tpv = json.load(f)
            else:
                tpv = {}
            if 'lat' in tpv:
                gps_filename = filename.replace(".pcap", ".gps.json")
                logging.info(f"saving GPS to {gps_filename} ({tpv})")
                with open(gps_filename, "w+t") as fp:
                    json.dump(tpv, fp)
                    fp.write(",\n")
            else:
                logging.info("not saving GPS. Couldn't find location.")
        except Exception as err:
            logging.exception("[pwn-gpsd handshake] %s" % repr(err))

    def on_webhook(self, path, request):
        try:
            method = request.method
            path = request.path
            if "/zoom_in" in path:
                self.zoom_in("web")
                return "OK", 204
            elif "/zoom_out" in path:
                self.zoom_out("web")
                return "OK", 204
            else:
                return "<html><body>PeerMap! %s:<p>Request <a href=\"/plugins/peer_map/zoom_in\">/plugins/peer_map/zoom_in</a> <a href=\"/plugins/peer_map/zoom_out\">/plugins/peer_map/zoom_out</a></body></html>" % (path)
        except Exception as e:
            logging.exception(e)
            return "<html><body>Error! %s</body></html>" % (e)

