import logging

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import *
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.mesh.peer import Peer

from sympy import Point, Line

try:
    import matplotlib.pyplot as plt
    import matplotlib as mpl
except Exception as e:
    logging.warning("Install matplotlib to get better performance")
    logging.warning("\t sudo apt-install python3-matplotlib")
    plt = None

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
except Exception as e:
    logging.warning("Install cartopy to get better maps")
    logging.warning("\t sudo apt-install python3-cartopy python-cartopy-data")
    ccrs = None

import threading
from threading import Event

from io import BytesIO

from math import ceil, floor
import os
import glob
from datetime import datetime,timedelta
import time
#import json
try:
    import orjson as json
except Exception as e:
    logging.warning("Install orjson with pip to get better json performance")
    import json

import random
import hashlib
import base64
from urllib.parse import urlparse,unquote
from cryptography.fernet import Fernet
import prctl

from math import radians, sin, cos, acos

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
        logging.warning("Unable to process: Overall %d elements. New %d elements:  %s" % (len(overall), len(new), repr(new)))

    return ret

def boxesOverlap(a, b):
    try:
        if not b:
            logging.error("None type given")
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
    last_point = None   # GPSD TPV structures
    lons = []     # array of longitudes cached from tpvs
    lats = []     # array of latitudes taken from tpvs
    bounds = None
    visible = True
    zoomToFit = False
    
    def __init__(self, name, filename=None, visible=True, zoomToFit=False, verbose=False, keep_going=True):
        self.name = name
        self.filename = filename
        self.visible = visible
        self.zoomToFit = zoomToFit
        self.gpio = None
        self.verbose = verbose
        self.keep_going = keep_going

        if filename:
            self.loadFromFile(filename, ifUpdated=False)
            if self.verbose:
                logging.info("Loaded %s %s" % (len(self.lats), filename))
            else:
                logging.debug("Loaded %s %s" % (len(self.lats), filename))

    def addPoint(self, tpv):
        if not 'lat' in tpv and 'Latitude' in tpv:
            tpv['lat'] = tpv['Latitude']
            tpv['lon'] = tpv['Longitude']

        if not 'lat' in tpv and 'location' in tpv:
            if 'lat' in tpv['location'] and 'lng' in tpv['location']:
                tpv['lat'] = tpv['location']['lat']
                tpv['lon'] = tpv['location']['lng']
                logging.info("Adding %s: %s %s" % (self.name, tpv['lat'], tpv['lon']))
            else:
                logging.info("UNKNOWN: %s %s" % (self.filename, tpv))
                
        if 'lat' in tpv and 'lon' in tpv:

            lat = tpv.get('lat')
            lon = tpv.get('lon')

            # consider dumping previous point based on slope
            if len(self.lats) > 2:
                p0 = (self.lons[-2], self.lats[-3])
                p1 = (self.lons[-1], self.lats[-2])
                p2 = (lon, lat)

                def slope(p0, p1):
                    return  (p1[1] - p0[1])/(p1[0]-p0[0])
                
                m01 = slope(p0, p1)
                m02 = slope(p0, p2)

                if abs(m01-m02) < 0.2:
                    del(self.lons[-1])
                    del(self.lats[-1])
                    #logging.warning("Removing point: %s %s - %s < thresh. %d remain" % (p1, m01, m02, len(self.lats)))
                elif False or (abs(p2[0]-p0[0]) < 0.000025 and abs(p2[1]-p0[1] < 0.000025)):
                    del(self.lons[-1])
                    del(self.lats[-1])
            
            self.last_point = tpv
            self.lons.append(lon)
            self.lats.append(lat)

            if not self.bounds:
                self.bounds = [200,200,-200,-200]

            if lon < self.bounds[0]: self.bounds[0] = lon
            if lat < self.bounds[1]: self.bounds[1] = lat
            if lon > self.bounds[2]: self.bounds[2] = lon
            if lat > self.bounds[3]: self.bounds[3] = lat

            
    def lastPoint(self):
        logging.debug("LAST POINT IS %s (%s)" % (self.last_point, len(self.lats)))
        return self.last_point
            
    def loadFromFile(self, filename, ifUpdated=True, ifOlderThan=0):
        try:
            now = time.time()
            if now - self.mtime < ifOlderThan:
                # wait at least 10 seconds between reloads
                if self.verbose:
                    logging.info("too new")
                return False

            if filename and os.path.isfile(filename):
                if self.verbose:
                    logging.info("file %s exists" % (filename))
                self.filename = filename
                mtime = os.stat(filename).st_mtime
                if ifUpdated == False or mtime > self.mtime:
                    if True or self.verbose:
                        logging.info("loading %s" % filename)
                    self.mtime = mtime
                    lines = []
                    with open(filename) as f:
                        lines = [line.rstrip().strip(',') for line in f]
                    tmp = gpsTrack(filename, keep_going=self.keep_going)
                    tmp.lons=[]
                    tmp.lats=[]

                    for l in lines:
                        try:
                            if not self.keep_going:
                                break
                            tpv = json.loads(l)
                            tmp.addPoint(tpv)
                        except Exception as e:
                            logging.debug("- skip line: %s %s" % (os.path.basename(filename), e))
                    if True or self.verbose:
                        logging.info("Loaded %s %d lines, %d steps within %s" % (os.path.basename(filename), len(lines), len(tmp.lats), tmp.bounds))
                    if len(tmp.lats):
                        self.bounds = tmp.bounds
                        self.lons = tmp.lons.copy()
                        self.lats = tmp.lats.copy()
                        self.last_point = tmp.last_point
                        del tmp
                        return True
                    else:
                        logging.error("Empty track: %s" % filename)
                return False
            else:
                logging.warn("No track file: %s" % (filename))
                return False
        except Exception as e:
            logging.exception(e)
        return False

    def reloadFile(self, ifOlderThan=1):
        return self.loadFromFile(self.filename, ifOlderThan=ifOlderThan)

class Peer_Map(plugins.Plugin, Widget):
    __author__ = 'Sniffleupagus'
    __version__ = '1.0.5'
    __license__ = 'GPL3'
    __description__ = 'Plot gps tracks on pwnagotchi screen'

    def __init__(self):
        super().__init__(None)
        self._agent = None
        self._ui = None
        self.password = "Friendship"
        self.fernet = None
        self.ui_elements = []
        self.me = None
        self.tracks = {}
        self.peers = {}
        self.hs_tracks = {}
        self.redrawImage = False
        self.image = None
        self.value = None
        self.xy = None
        self.t_dir = None
        self.font = None
        self.touch_info = {}
        self.gpio = None
        self.window_size = None
        self.keep_going = True
        self._worker_thread = None
        self._load_tracks_thread = None
        self.trigger_redraw = Event()
        self.occupado = False
        self.potfile_mtime = 0
        self.ap_names = []
        self.cracked = {}

        self.event_handler = "peer_map"

        self.state = True # this makes it touchable in Touch_UI plugin
        
        self.track_colors=['#00ff00', '#ffff00', '#ff00ff', '#00ffff', '#40ff40', '#ff8080', '#c0c0ff', '#40c080', '#80c040', '#80c080', '#800000', '#404080'] # a bunch of colors
        self.peer_colors=['red', 'blue', 'purple', 'orange', 'brown']

    def haversine_distance(self, ln1, lt1, ln2, lt2):
        from math import radians, sin, cos, acos

        mlat = radians(float(lt1))
        mlon = radians(float(ln1))
        plat = radians(float(lt2))
        plon = radians(float(ln2))

        dist = 6371.01 * acos(sin(mlat)*sin(plat) + cos(mlat)*cos(plat)*cos(mlon - plon))
        logging.debug("The distance is %.2fkm." % dist)
        return dist *1000

    def _worker(self):
        try:
            prctl.set_name("peer_map drawer")
        except:
            logging.info("No rename")

        while self.keep_going:
            try:
                self.check_tracks_and_peers()
                if self.trigger_redraw.is_set():
                    self.trigger_redraw.clear()

                    if self.redrawImage:
                        logging.debug("Redrawing image")
                        now = time.time()
                        self.updateImage()
                        self.redrawImage = False
                        if time.time() - now > 10:
                            logging.warn("Slow redraw %fs" % (time.time() - now))
                        time.sleep(5)
                    else:
                        self.trigger_redraw.wait(timeout=2)
                else:
                    logging.debug("timeout")
                    self.trigger_redraw.wait(timeout=2)
            except Exception as e:
                logging.exception("PM_Drawer: %s" % (e))
        logging.debug("peer_map drawing thread exiting")

    def updateImage(self):
      try:
        if self.occupado or not self._ui:
            return
        self.occupado = True
        logging.debug("Updating")
        w = self.xy[2]-self.xy[0]
        h = self.xy[3]-self.xy[1]

        zoom_multiplier = self.options.get('zoom', 0.9)

        self.redrawImage = False
        then = time.time()

        # compute lon/lat boundaries
        if self.me and self.me.bounds:
            bounds = self.me.bounds.copy()
            logging.debug("Me: %s" % (self.me.bounds))
        else:
            bounds = [180,90,-180,-90]   # the whole world
        for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible and t.zoomToFit:
                bounds = checkBounds(bounds, t.bounds)
        logging.debug("Track bounds: %s" % (bounds))

        pbounds = [180,90, -180,-90]
        logging.debug("Unpacking peers: %s" % (repr(self.peers)))
        for p,info in self.peers.items():
            try:
                tpv = info['tpv']
                pbounds = checkBounds(pbounds, tpv)
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

        logging.debug("Final w=%s, h=%s,  bounds(%fs): %s" % (w,h, time.time()-then, bounds))

        scale = min((w)/sw, (h)/sh) * zoom_multiplier    # pixels per map unit
        if (zoom_multiplier >= 1) and self.me and self.me.bounds:
            midpoint = [self.me.bounds[0], self.me.bounds[1]]
        else:
            midpoint = [(bounds[2]+bounds[0])/2, (bounds[3]+bounds[1])/2]
            logging.info("midpoint not me")
            midpoint = [self.me.bounds[0], self.me.bounds[1]]

        map_bbox = [midpoint[0] - (w/2.0)/scale, midpoint[1] - (h/2.0)/scale,
                    midpoint[0] + (w/2.0)/scale, midpoint[1] + (h/2.0)/scale]

        if map_bbox[0] < -180:
            map_bbox[0] = -180
        if map_bbox[1] < -90:
            map_bbox[1] = -90
        if map_bbox[2] > 180:
            map_bbox[2] = 180
        if map_bbox[3] > 90:
            map_bbox[3] = 90

        midpoint = [(map_bbox[2]+map_bbox[0])/2, (map_bbox[3]+map_bbox[1])/2]

        cw,ch = (map_bbox[2]-map_bbox[0])*scale,(map_bbox[3]-map_bbox[1])*scale
        if int(cw) != w or int(ch) != h:
            logging.debug("Computed size: (%i, %i) != (%i, %i)" % (cw,ch, w, h))
        fig = None
        ax = None
        image = None
        d = None
        if plt:
            mpl.rcParams["figure.dpi"]=100
            mpl.rcParams['text.antialiased'] = False
            dpi = mpl.rcParams["figure.dpi"]
            mpl.rcParams["path.simplify"] = True
            linewidth = dpi * 0.1
            fig = plt.figure(figsize=((w)/dpi, (h)/dpi), facecolor=self.bgcolor)
            ax = fig.add_subplot(1, 1, 1) if not ccrs else fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())

            fig.tight_layout()
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            plt.xticks([])
            plt.yticks([])
            plt.axis('off')
            plt.xlim(map_bbox[0], map_bbox[2])
            plt.ylim(map_bbox[1], map_bbox[3])
            logging.debug("DPI = %s, w = %s, h = %s, gca = %s" % (dpi, w, h, fig.gca()))
            if ccrs and self.image:
                try:
                    wlon = map_bbox[2]-map_bbox[0]
                    if wlon < 10:
                        fscale = '10m'
                    elif wlon < 50.0:
                        fscale = '50m'
                    else:
                        fscale = '110m'
                    feats = self.options.get("map_features", ["LAND", "LAKES", "RIVERS", "STATES"])
                    if 'OCEAN' in feats:  ax.add_feature(cfeature.OCEAN.with_scale('110m'), zorder=1, linewidth=.1, edgecolor='b')
                    if 'LAND' in feats:   ax.add_feature(cfeature.LAND.with_scale('110m'), zorder=1, linewidth=.1, edgecolor='b')
                    if 'LAKES' in feats:  ax.add_feature(cfeature.LAKES.with_scale(fscale), zorder=3, linewidth=.1, edgecolor='LightBlue')
                    if 'RIVERS' in feats: ax.add_feature(cfeature.RIVERS.with_scale(fscale), zorder=3, linewidth=.1, edgecolor='Blue', linestyle=':')
                    if 'STATES' in feats: ax.add_feature(cfeature.STATES.with_scale(fscale), zorder=3, linewidth=.1, edgecolor='DarkGrey', linestyle=':')
                        
                    logging.warn("Finished features (%fs)" % (time.time()-then))
                except Exception as e:
                    logging.exception(e)
        else:
            # use PIL
            logging.debug("PIL")
            image = Image.new('RGBA', (w,h), self.bgcolor)
            d = ImageDraw.Draw(image)
            d.fontmode = '1'
            d.rectangle((0,0,w-1,h-1), fill=self.bgcolor, outline='#808080')

        # draw tracks
        i = 0
        if self.options.get('show_tracks', True) and self.image:
          for f in sorted(self.tracks):
            t = self.tracks[f]
            if t.visible and boxesOverlap( map_bbox, t.bounds) and self.keep_going:
                # visible and overlaps, so plot it
                logging.debug("Plotting %s %s" % (f, t.bounds))
                lp = None
                color = self.track_colors[i % len(self.track_colors)]
                logging.debug("Scale: %s, %s, map box: %s" % (scale, color, map_bbox))
                if plt:
                    try:
                        logging.debug("Plotting (%fs) %d, %d %s %s" % (time.time()-then, len(t.lons), len(t.lats), f, color))
                        plt.plot(t.lons, t.lats, zorder=4, marker=',', markersize=linewidth, linewidth=0, markeredgecolor='none', color=color, antialiased=False, alpha=0.3)
                    except Exception as e:
                        logging.exception("Plot: Lats %d, lons %d, err: %s" % (len(lats), len(lons), e))
                else:
                    for p in range(len(t.lons)):
                        x = (t.lons[p] - midpoint[0]) * scale + w/2
                        y = (t.lats[p] - midpoint[1]) * scale + h/2
                        d.point((x, h-y), fill = color)
                i += 1
                logging.debug("Track (%fs) %d, %d %s" % (time.time()-then, len(t.lons), len(t.lats), f))
        logging.warn("Drew tracks (%fs) (%s %s)" % (time.time()-then, w,h))

        # draw peers
        i = 1
        for p, info in self.peers.items():
            data = info.get('tpv', {})
                    
            if 'lat' in data and 'lon' in data:
                pc = self.peer_colors[i % len(self.peer_colors)]
                if plt:
                    plt.plot(data['lon'], data['lat'], zorder=5, marker='o', markersize=2, color=pc)
                    if self.options.get('peer_names', True):
                        name = data.get('name', "XXX")
                        if not self.window_size:
                            name = name[0:3]
                        plt.text(data['lon'], data['lat'], name, va='top', ha='left', zorder=5, color=pc, fontsize=8)
                else:
                    x = (data['lon'] - midpoint[0]) * scale + w/2
                    y = (data['lat'] - midpoint[1]) * scale + h/2
                    d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=pc)
                    tbox = self.font.getbbox(data.get('name', "XXX"))
                    xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
                    yoff = int(0 if (y-tbox[3]) > 0 else tbox[3])
                    name = data.get('name', "XXX")
                    if not self.window_size:
                        name = name[0:3]
                    d.text((x+xoff,h-(y+yoff)), name, fill=pc, font=self.font)

                logging.debug("Plot peer (%fs): %s, %s" % (time.time()-then, p, data))
                i += 1

        # draw handshakes
        i = 0
        for f in list(self.hs_tracks.keys()):
          try:
            t = self.hs_tracks[f]
            if not t.bounds:
                continue
            if t.visible and boxesOverlap( map_bbox, t.bounds) and self.keep_going:
                # visible and overlaps, so plot it
                logging.debug("Plotting HS %s %s" % (f, t.bounds))
                lp = t.lastPoint()
                color = self.track_colors[i % len(self.track_colors)]
                if plt:
                    try:
                        logging.debug("Handshake plot (%fs) %d, %d %s %s" % (time.time()-then, len(t.lons), len(t.lats), f, color))
                        plt.plot(t.lons, t.lats, zorder=4, marker=',', markersize=linewidth, linewidth=1, markeredgecolor='none',
                                 color='Black', antialiased=False, alpha=0.5)
                        lmark = '*' if f in self.ap_names else 'x'
                        plt.plot(lp['lon'], lp['lat'], zorder=5, marker=lmark, markersize=5, color=color, alpha=1.0, antialiased=False)
                        lcolor = ('Blue' if f in self.cracked else 'Green') if f in self.ap_names else ('Orange' if f in self.cracked else 'Red')
                        if self.options.get('hs_names', True):
                            lab = f[0:4] # if not self.window_size else f
                            if not f in self.cracked:
                                lab = lab.lower()
                            plt.text(lp['lon'], lp['lat'], lab, va='top', ha='right', zorder=5, color=lcolor, fontsize=6, alpha=0.7)

                    except Exception as e:
                        logging.exception("Plot: Lats %d, lons %d, err: %s" % (len(t.lats), len(t.lons), e))
                else:
                    for p in range(len(t.lons)):
                        x = (t.lons[p] - midpoint[0]) * scale + w/2
                        y = (t.lats[p] - midpoint[1]) * scale + h/2
                        d.point((x, h-y), fill = color)
                i += 1
                logging.debug("Handshake (%fs) %d, %d %s" % (time.time()-then, len(t.lons), len(t.lats), f))
          except Exception as he:
              logging.error("HS %s: %s" % (f, he))
        logging.debug("Drew handshakes (%fs) (%s %s)" % (time.time()-then, w,h))

        # draw me
        if self.me and self.me.bounds:
            logging.debug("Me: %s" % (self.me.bounds))
            data = self.me.lastPoint()
            if plt:
                plt.plot(data['lon'], data['lat'], zorder=5, marker='*', markersize=6, color='red', alpha=0.5)
                #plt.text(data['lon'], data['lat'], 'me', va='top', ha='right', zorder=5, color='Red', fontsize=8)
            else:
                # without matplotlib
                x = (self.me.bounds[0] - midpoint[0]) * scale + w/2
                y = (self.me.bounds[1] - midpoint[1]) * scale + h/2
                d.ellipse((x-1, h-y-1, x+1, h-y+1), fill=self.peer_colors[0])
                tbox = self.font.getbbox("me")
                xoff = int(0 if x+tbox[2] < w else (w - (x+tbox[2])))
                yoff = int(0 if (y-tbox[3]) > 0 else tbox[3]+2)
                logging.debug("Offset: %s %s = %s" % (xoff, yoff, self.color))
                d.text((x+xoff,h-(y+yoff)), "me", fill=self.color, font=self.font)

        # convert matplotlib fig to PIL image
        if plt:
            try:
                logging.debug("Doing buf (%fs)" % (time.time()-then))
                buf = BytesIO()
                #fig.set_facecolor(self.bgcolor)
                fig.savefig(buf, pad_inches=0, bbox_inches='tight')
                ax.remove()
                plt.clf()
                plt.close(fig)
                buf.seek(0)
                logging.debug("Loading buf (%fs)" % (time.time()-then))
                pmap =  Image.open(buf)
                logging.debug("Loaded buf (%fs) %s" % (time.time()-then, pmap.size))
                del buf
                image = Image.new('RGBA', (w, h), self.bgcolor)
                image.paste(pmap, (0,0))
                d = ImageDraw.Draw(image)
                d.fontmode = '1'
                d.rectangle((0,0,w-1,h-1), outline='#808080')
            except Exception as e:
                logging.exception(e)
                
        # draw legend and grid on full screen
        if self.window_size:
            try:
                dist = self.haversine_distance(map_bbox[0], 0, map_bbox[2], 0)
                units = self.options.get('units', 'metric').lower()
                logging.debug("Distance is: %s. Want units %s" % (dist, units))
                if units in ['feet', 'imperial']:
                    dist *= 3.28084 # meters to feet
                    if dist > 5280: # show miles if far
                        dist_text = "W: %0.2f mi, %0.5e°" % (dist/5280.0, map_bbox[2]-map_bbox[0])
                        dist_text += "\nH: %0.2f mi, %0.5e°" % (dist * h / w / 5280.0, map_bbox[3]-map_bbox[1])
                    else:
                        dist_text="W: %0.2f feet, %0.5e°" % (dist, map_bbox[2]-map_bbox[0])
                        dist_text += "\nH: %0.2f feet, %0.5e°" % (dist * h / w, map_bbox[3]-map_bbox[1])
                else:
                    if dist > 1000: # km or meters
                        dist_text = "W: %0.2f km, %0.5e°" % (dist/1000.0, map_bbox[2]-map_bbox[0])
                        dist_text += "\nH: %0.2f km, %0.5e°" % (dist * h / w / 1000.0, map_bbox[3]-map_bbox[1])
                    else:
                        dist_text = "W: %0.2f m, %0.5e°" % (dist, map_bbox[2]-map_bbox[0])
                        dist_text += "\nH: %0.2f m, %0.5e°'" % (dist * h / w, map_bbox[3]-map_bbox[1])
                d.text((5,5), "%s\nZ: %s" % (dist_text, zoom_multiplier), fill=self.color, font=self.font)
                logging.debug("Window %s" % dist_text)
            except Exception as e:
                logging.exception(e)

        self.image = image
      except Exception as e:
          logging.exception(e)
      logging.warn("Updated (%fs) %s %s" % (time.time()-then,w,h))
      self._ui.set('peer_map', time.time())

      self.occupado = False

    def draw(self, canvas, drawer):
        if not self.image:
            return
            w = self.xy[2]-self.xy[0]
            h = self.xy[3]-self.xy[1]
            im = Image.new('RGBA', (w,h), self.bgcolor)
            d = ImageDraw.Draw(im)
            d.rectangle((0,0,w,h), outline='#808080')
            d.text((w/2,h/2), "Peer Map", anchor="mm", font=self.font, fill="Red")
            self.image = im.resize(w,h)
                        
        if self.image and self.xy:
            try:
                canvas.paste(self.image.convert(canvas.mode), self.xy)
            except Exception as e:
                w = self.xy[2]-self.xy[0]
                h = self.xy[3]-self.xy[1]
                logging.error("Paste: %s, %s, want (%s, %s): %s" % (self.xy, self.image.size, w, h, e))
                self.image = self.image.resize((self.xy[2]-self.xy[0], self.xy[3]-self.xy[1]))
                try:
                    canvas.paste(self.image.convert(canvas.mode), (self.xy[0], self.xy[1]))
                    self.redrawImage = True
                    self.trigger_redraw.set()
                    #self.image = None
                except Exception as e2:
                    logging.exception("Resized error: %s" % e2)


    def readPotfile(self, fname="/root/handshakes/wpa-sec.cracked.potfile"):
        if os.path.isfile(fname):
            st = os.stat(fname)
            mtime = st.st_mtime if st else 0

            if mtime == self.potfile_mtime:
                logging.debug("Potfile unchanged.")
            else:
                logging.info("Reading potfile.")
                self.potfile_mtime = mtime
                with open(fname) as f:
                    self.cracked = {}
                    for line in f:
                        (mac, othermac, ssid, info) = line.strip().split(':', 3)
                        self.cracked[mac.lower()] = line
                        self.cracked[ssid] = line
            logging.info("Read %d passwords" % (len(self.cracked.keys())))

    def on_ready(self, agent):
        self._agent = agent
        try:
            logging.getLogger().setLevel(logging.INFO)
            count = 0
            nocount = 0
            self.readPotfile()
            # load handshake locations
            for fname in glob.glob("%s/*_*g[ep][os]*json" % agent._config['bettercap'].get('handshakes', '/root/handshakes')):
                fbase = os.path.basename(fname)
                try:
                    (ssid, mac) = fbase.split('_', 1)
                    if self.options.get('show_uncracked', False)  or ssid in self.cracked:
                        t =  gpsTrack(ssid, filename=fname, visible=True, zoomToFit=True, verbose=False)
                        if len(t.lats) > 0:
                            self.hs_tracks[ssid] = t
                            count += 1
                            logging.debug("%d/%d Loaded cracked handshake %s: %s" % (count, nocount, fname, len(t.lats)))
                        else:
                            nocount += 1
                            logging.debug("No track for %s: %s" % (fname, t))
                    else:
                        nocount += 1
                except Exception as e:
                    logging.exception(e)
        except Exception as e:
            logging.exception(e)
        logging.info("Have %d handshakes" % (len(self.hs_tracks.keys())))

    def on_loaded(self):
      try:
        logging.debug("peer_map loaded with options %s" % (self.options))
        self.password = self.options.get('password', None)
        if self.password:
            self.fernet = Fernet(self.generateKey())

        self.gpio = self.options.get("gpio", None)
        if self.gpio:
            try:
                GPIO.setmode(GPIO.BCM)
                for action in ['zoom_in', 'zoom_out', 'toggle_fs']:
                    if action in self.gpio:
                        if action == 'zoom_in':
                            cb = self.zoom_in
                        elif action == 'zoom_out':
                            cb = self.zoom_out
                        elif action == 'toggle_fs':
                            cb = self.toggle_fs
                        else:
                            cb = self.handle_button
                        p = self.gpio[action]
                        logging.info("Setting up %s -> %s" % (action, p))
                        GPIO.setup(p, GPIO.IN, GPIO.PUD_UP)
                        logging.info("Setting event %s -> %s" % (action, p))
                        GPIO.add_event_detect(p, GPIO.FALLING, callback=cb,
                                   bouncetime=100)
                        logging.info("Set up %s on pin %d" % (action, p))
            except Exception as gpio_e:
                logging.exception("Loading GPIO: %s" % gpio_e)

        if 'track_colors' in self.options:
            self.track_colors = self.options['track_colors']
        if 'peer_colors' in self.options:
            self.peer_colors = self.options['peer_colors']

        self.t_dir = self.options.get("track_dir", "/etc/pwnagotchi/pwn_gpsd")
        self._load_tracks_thread = threading.Thread(target=self.load_gps_tracks, args=())
        self._load_tracks_thread.start()

      except Exception as e:
        logging.exception(e)

    def load_gps_tracks(self):
      try:
        fname = os.path.join(self.t_dir, "current.txt")
        if os.path.isfile(fname):
            self.me = gpsTrack("current", fname, True, True, keep_going=self.keep_going)
            logging.info("Read my location: %s" % (self.me.lastPoint()))
            self.redrawImage = True
            self.trigger_redraw.set()


        now = datetime.now()

        tracks_fname_fmt = self.options.get("track_fname_fmt", "pwntrack_%Y%m%d.txt")
        n = 0
        i = 0
        num_days = self.options.get("days", 3)
        while i < num_days*3 and n < num_days and self.keep_going:
            fname = (now - timedelta(days=i)).strftime(tracks_fname_fmt)
            logging.debug("Looking for %s" % os.path.join(self.t_dir, fname))
            if os.path.isfile(os.path.join(self.t_dir, fname)):
                t = gpsTrack(fname, os.path.join(self.t_dir, fname), True, True)
                self.tracks[fname] = t
                n += 1
                self.redrawImage = True
                self.trigger_redraw.set()
            i += 1

        tracks_fname_fmt = self.options.get("track_fname_fmt", "peertrack_%Y%m%d.txt")
        n = 0
        i = 0
        while i < 30 and n < self.options.get("days", 3) and self.keep_going:
            fname = (now - timedelta(days=i)).strftime(tracks_fname_fmt)
            logging.debug("Looking for %s" % os.path.join(self.t_dir, fname))
            if os.path.isfile(os.path.join(self.t_dir, fname)):
                t = gpsTrack(fname, os.path.join(self.t_dir, fname), True, True)
                self.tracks[fname] = t
                n += 1
                self.redrawImage = True
                self.trigger_redraw.set()
            i += 1

      except Exception as e:
          logging.exception(e)
      logging.info("peer_map tracks loaded")

    def zoom_in(self, channel):
      try:
        if not self._ui:
            return
    
        self.options['zoom'] = self.options.get('zoom', 0.9) * 2.0
        self.redrawImage = True
        self.trigger_redraw.set()
        logging.info("Zoom multiplier = %s" % self.options['zoom'])
        self._ui.set('peer_map', time.time())
      except Exception as e:
          logging.exception("Zoom in: %s: %s" % (channel, e))

    def zoom_out(self, channel):
      try:
        if not self._ui:
            return

        self.options['zoom'] = self.options.get('zoom', 0.9) / 2.0
        self.redrawImage = True
        self.trigger_redraw.set()
        logging.info("Zoom multiplier = %s" % self.options['zoom'])
        self._ui.set('peer_map', time.time())
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
                    logging.debug("Decrypted (%s): %s" % (type(decrypted_message).__name__, decrypted_message))
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
        logging.debug("Touch release: %s, %s" % (touch_data, ui_element));
        if ui_element != "peer_map":
            logging.warn("Touch release but not my element")
            return
    
        if not self.window_size:
            self.toggle_fs("touch")
        elif touch_data['point'][0] > 2*ui.width()/3:
            self.zoom_in("touch")
        elif touch_data['point'][0] < ui.width()/3:
            self.zoom_out("touch")
        else:
            self.toggle_fs("touch")
        ui.set("peer_map", time.time())

    def on_unload(self, ui):
        self.keep_going = False
        self.trigger_redraw.set()
        with ui._lock:
            for el in self.ui_elements:
                try:
                    logging.debug("Removing %s" % el)
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
        if self._worker_thread:
            logging.debug("Waiting for drawing thread to finish")
            try:
                self._worker_thread.join()
            except Exception as e:
                logging.exception(e)
        if self._load_tracks_thread:
            logging.debug("Waiting for drawing thread to finish")
            try:
                self._load_tracks_thread.join()
            except Exception as e:
                logging.exception(e)
        logging.info("Unloaded")

    def on_ui_setup(self, ui):
        self._ui = ui
        if not self._agent:
          self._agent = ui._agent
        try:
            self.xy = self.options.get("pos", [170,42,250,100])
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
            #self._worker_thread = _thread.start_new_thread(self._worker, ())
            self._worker_thread = threading.Thread(target=self._worker, args=())
            self._worker_thread.start()
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
                e_msg = adv.get(ADV_FIELD, None)
                info = self.peers.get(id, {})  # stored peer data
                if e_msg and e_msg != info.get('enc', None):  # only process if message changed
                    try:
                        raw = self.decrypt_data(e_msg)
                        if raw and raw != info.get('raw', ''):  # double check
                            try:
                                data = json.loads(raw)  # read new peer info into a dict
                                if 'lat' in data and 'lon' in data:
                                    logging.debug("Saving PEER %s: %s" % (p.adv.get('name', None), data))
                                    name = p.adv.get('name', "peer")
                                    data['name'] = name
                                    self.peers[id] = {'enc': e_msg, 'raw': raw, 'tpv': data, 'name': name }
                                    ret = True
                            except Exception as e:
                                logging.error("JSON.loads(%s) %s" % (raw, e))
                        else:
                            logging.warn("New encrypt, same raw")
                    except Exception as e:
                        logging.exception(e)
        return ret
      except Exception as e:
          logging.exception(e)
          return ret

    def check_tracks_and_peers(self):
        # check peers
        if self.update_peers():
            logging.debug("Peers changed")
            self.redrawImage = True

        if self.me and self.me.reloadFile():
            logging.debug("My location changed")
            self.redrawImage = True
            
        # check tracks less often
        for f in sorted(self.tracks):
            logging.debug("Checking %s" % f)
            t = self.tracks[f]

            if t.visible and t.reloadFile(ifOlderThan=60):
                logging.debug("%s CHANGED" % f)
                self.redrawImage = True

        if self.redrawImage:
            logging.debug("REDRAW set")
            self.trigger_redraw.set()
        return self.redrawImage

    def on_ui_update(self, ui):
        bounds = [180,90,-180,-90]

        # check me
        if self.me:
            logging.debug("Me updated: %s" % (self.me.bounds))
            self.redrawImage = True
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
                    fix += "-%d" % (int(tpv['undivided_count'][0]))

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
                with open(gps_filename, "wb+") as fp:
                    out = json.dumps(tpv)
                    if isinstance(out, str):
                        out = out.encode()
                    fp.write(out)
                    fp.write("\n".encode("utf-8"))
            else:
                logging.warning("not saving GPS. Couldn't find location.")
        except Exception as err:
            logging.exception("[pwn-gpsd handshake] %s" % repr(err))


    def toggle_fs(self, channel):
        if self.window_size:
            self.xy = self.window_size.copy()
            self.window_size = None
            logging.info("Toggle to windowed")
        else:
            self.window_size = self.xy.copy()                    
            border = self.options.get('border', 5)
            self.xy = (border, border, self._ui.width()-border, self._ui.height()-border)
            logging.info("Toggle to fullscreen")
        self.redrawImage = True
        self.trigger_redraw.set()
        self._ui.set('peer_map', time.time())

    def on_webhook(self, path, request):
        try:
            method = request.method
            path = request.path
            logging.debug("Webhook %s %s" % (path, repr(request.args)))
            if "/zoom_in" in path:
                self.zoom_in("web")
                self._ui.set('peer_map', time.time())
                return "OK", 204
            elif "/zoom_out" in path:
                self.zoom_out("web")
                self._ui.set('peer_map', time.time())
                return "OK", 204
            elif "/toggle_fs" in path:
                self.toggle_fs("web")
                self.trigger_redraw.set()
                self._ui.set('peer_map', time.time())
                return "OK", 204
            elif "/set_zoom" in path:
                try:
                    logging.info("Args: %s" % (repr(request.args)))
                    zf = int(request.args.get('zf', self.options.get('zoom')))
                    #if zf < 1:
                    #    zf = 1       
                    if zf != self.options.get('zoom'):
                        self.options['zoom'] = zf
                        self.redrawImage = True
                        self.trigger_redraw.set()
                        self._ui.set('peer_map', time.time())
                    return "OK", 204
                
                except Exception as e:
                    logging.exception(e)
                    return "<html><body>%s</body></html>" % (e)
            elif "/set" in path:
                try:
                    logging.info("Setting all settings: %s" % repr(request.args))
                    ret = '<html>><body>PeerMap! %s:<p>' % (path)
                    ret += '<form action="/plugins/peer_map/set" method=get">'
                    ret += '<ul>\n'

                    allowed_options={"units:string":"feet|imperial|metric",
                                     "days:int":"^[0-9]+$",
                                     "zoom:float":"",
                                     "hs_names:bool":"True|False",
                                     "peer_names:bool":"True|False",
                                     }
                    for a in request.args:
                        if ':' in a:
                            o, t = a.split(":")
                            logging.info("args %s(%s) = %s" % (a, t, request.args[a]))
                            if a in allowed_options:
                                logging.info("\tUpdating %s %s -> %s" % (a, self.options[o], request.args[a]))
                                if t == "int":
                                    self.options[o] = int(request.args[a])
                                elif t == "float":
                                    self.options[o] = float(request.args[a])
                                elif t == "bool":
                                    if request.args[a].lower() == "true":
                                        self.options[o] = True
                                    elif request.args[a].lower() == "false":
                                        self.options[o] = False
                                    else:
                                        logging.error("Invalid boolean value")
                                elif t == "str":
                                    self.options[o] = request.args[a]
                                else:
                                    logging.info("Unsupported options")

                    # zoom multiplier
                    #if 'zf' in request.args:
                    #    try:
                    #        zm = int(request.args['zf'])
                    #        self.options['zoom'] = zm
                    #        self.redrawImage = True
                    #    except Exception as e:
                    #        ret += "<li>Error on zoom multiplier: %s" % e
                            
                    #ret += '<li>Zoom Factor<input type=number id="zf" name="zf" step=".001" value="%f" />\n' % self.options.get('zoom')

                    for o in self.options:
                        logging.debug("option %s -> %s" % (o, self.options[o]))
                        t = type(o).__name__
                        if t in ["int", "float", "str", "bool"]:
                            ret += '<li>%s<input type=text name="%s:%s" value="%s">\n' % (o, o, t, self.options[o])

                    ret += "</ul><input type=submit name=Update value=Update></form>"
                    ret += "</body></html>"
                    return ret
                except Exception as e:
                    logging.exception(e)
                    ret += "<p><h3>ERROR: %s</h3></body></html>" % e
                    return ret
            else:
                return("<html><body>PeerMap! %s:<p>Request " % (path)
                       + "<a href=\"/plugins/peer_map/zoom_in\">/plugins/peer_map/zoom_in</a>"
                       + " <a href=\"/plugins/peer_map/zoom_out\">/plugins/peer_map/zoom_out</a>"
                       + " <a href=\"/plugins/peer_map/toggle_fs\">/plugins/peer_map/toggle_fs</a>"
                       + "</body></html>")
        except Exception as e:
            logging.exception(e)
            return "<html><body>Error! %s</body></html>" % (e)


    def on_unfiltered_ap_list(self, agent, access_points):
        try:
            self.ap_names = [ap['hostname'] for ap in access_points if (ap['hostname'] and ap['hostname'] != '<hidden>')]
            logging.debug("Visible APS: %s" % (self.ap_names))
        except Exception as e:
            logging.exception(e)
