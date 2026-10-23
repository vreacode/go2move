"""Read-only saved-map previews and static observation candidates, not a planner.

Nav2 Foxy map_io trinary semantics (negate, thresholds, image y flip):
https://github.com/ros-navigation/navigation2/blob/foxy-devel/nav2_map_server/src/map_io.cpp
The map_saver unknown gray 205 is always blocked, even when an inconsistent
free_thresh would classify it as free. No ROS, live localization or motion API.
"""
import hashlib
import json
from datetime import datetime
import math
from pathlib import Path
import re

import cv2
import numpy as np
import yaml

MAPS_ROOT = Path('/home/unitree/go2_ros2_ws/maps')
CLEARANCE_M = .4
MIN_SEPARATION_M = .6
PREVIEW_MAX = 768
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}')


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + '必须是有限数值。')
    return float(value)


def pixel_to_world(column, row, data):
    resolution, (ox, oy, yaw) = data['resolution'], data['origin']
    x, y = (column+.5)*resolution, (data['height']-row-.5)*resolution
    return ox+math.cos(yaw)*x-math.sin(yaw)*y, oy+math.sin(yaw)*x+math.cos(yaw)*y


def world_to_pixel(x, y, data):
    ox, oy, yaw = data['origin']
    dx, dy = x-ox, y-oy
    local_x = math.cos(yaw)*dx+math.sin(yaw)*dy
    local_y = -math.sin(yaw)*dx+math.cos(yaw)*dy
    column = math.floor(local_x/data['resolution'])
    row = data['height']-1-math.floor(local_y/data['resolution'])
    if not 0 <= column < data['width'] or not 0 <= row < data['height']:
        raise ValueError('坐标不在地图范围内。')
    return column, row


class SavedMapRoutes:
    def __init__(self, maps_root=MAPS_ROOT):
        # Constructor override exists for isolated tests; HTTP accepts only map_id.
        self.root = Path(maps_root).resolve()

    def _registry(self):
        if not self.root.is_dir():
            return {}
        return {path.name: path for path in self.root.iterdir()
                if path.is_dir() and not path.is_symlink() and ID_PATTERN.fullmatch(path.name)
                and path.name not in ('.', '..') and (path/'map.yaml').is_file()}

    @staticmethod
    def _inside(path, directory):
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            raise ValueError('地图文件引用越界或使用符号链接。')
        return path

    def _load(self, map_id):
        registry = self._registry()
        if not isinstance(map_id, str) or map_id not in registry:
            raise ValueError('地图不存在，请选择已保存地图。')
        directory = registry[map_id]
        metadata = self._inside(directory/'map.yaml', directory)
        try:
            if metadata.stat().st_size > 65536:
                raise ValueError('地图描述文件过大。')
            yaml_bytes = metadata.read_bytes()
            values = yaml.safe_load(yaml_bytes)
            if not isinstance(values, dict):
                raise ValueError('地图YAML格式无效。')
            if values.get('mode', 'trinary') != 'trinary':
                raise ValueError('目前只支持trinary占据地图。')
            image_name = values.get('image')
            if not isinstance(image_name, str) or not image_name or '\x00' in image_name:
                raise ValueError('地图图像引用无效。')
            image_ref = Path(image_name)
            if '..' in image_ref.parts:
                raise ValueError('地图图像引用越界。')
            image_path = self._inside(image_ref if image_ref.is_absolute() else directory/image_ref, directory)
            if image_path.suffix.lower() != '.pgm' or image_path.stat().st_size > 32_000_000:
                raise ValueError('只支持本地图目录内的有限大小PGM。')
            resolution = _number(values.get('resolution'), '分辨率')
            if resolution <= 0:
                raise ValueError('地图分辨率必须大于零。')
            origin = values.get('origin')
            if not isinstance(origin, list) or len(origin) != 3:
                raise ValueError('地图origin必须有三个数值。')
            origin = [_number(item, 'origin') for item in origin]
            free_threshold = _number(values.get('free_thresh'), 'free_thresh')
            occupied_threshold = _number(values.get('occupied_thresh'), 'occupied_thresh')
            if not 0 <= free_threshold < occupied_threshold <= 1:
                raise ValueError('地图占据阈值无效。')
            negate = values.get('negate')
            if negate not in (0, 1, False, True):
                raise ValueError('地图negate须为0或1。')
            image_bytes = image_path.read_bytes()
            if image_bytes[:2] not in (b'P2', b'P5'):
                raise ValueError('地图PGM内容无效。')
            # Bound allocation from the PGM header before decoding pixels.
            tokens = re.findall(rb'#[^\n]*(?:\n|$)|[^\s#]+', image_bytes[:4096])
            header = [token for token in tokens if not token.startswith(b'#')][:4]
            if len(header) != 4:
                raise ValueError('地图PGM头无效。')
            width, height, maximum = map(int, header[1:])
            if min(width, height) <= 0 or width*height > 4_000_000 or maximum != 255:
                raise ValueError('地图图像尺寸或灰度位深不受支持。')
            image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
            if image is None or image.dtype != np.uint8 or image.ndim != 2 or image.shape != (height, width):
                raise ValueError('地图PGM图像无法读取。')
        except (OSError, yaml.YAMLError, TypeError, OverflowError) as exc:
            raise ValueError('地图文件无法读取：' + str(exc)[:100]) from exc
        occupancy = image.astype(np.float64)/255.
        if not negate:
            occupancy = 1.-occupancy
        unknown_guard = image == 205
        # Keep the saver unknown sentinel blocked despite legacy threshold mismatch.
        free = (occupancy < free_threshold) & ~unknown_guard
        occupied = (occupancy > occupied_threshold) & ~unknown_guard
        mismatch = bool(np.any(unknown_guard & (occupancy < free_threshold)))
        padded = np.pad(free.astype(np.uint8), 1, mode='constant')
        distances = cv2.distanceTransform(padded, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[1:-1,1:-1]
        # Center-to-center distance minus cell half diagonal bounds distance to a
        # whole obstacle/unknown cell; outside map is blocked by the padded border.
        clearance = np.maximum(0., distances*resolution-resolution/math.sqrt(2))
        safe = (free & (clearance >= CLEARANCE_M+1e-6)).astype(np.uint8)
        _, labels, statistics, centers = cv2.connectedComponentsWithStats(safe, connectivity=4)
        return {'id':map_id, 'name':map_id, 'directory':directory, 'width':width, 'height':height,
                'resolution':resolution, 'origin':origin, 'image':image, 'free':free,
                'occupied':occupied, 'clearance':clearance, 'labels':labels,
                'statistics':statistics, 'centers':centers, 'threshold_mismatch':mismatch,
                'map_sha256':hashlib.sha256(yaml_bytes+b'\0'+image_bytes).hexdigest(),
                'modified_at':max(metadata.stat().st_mtime, image_path.stat().st_mtime)}

    def _reference(self, data):
        rejection = None
        snapshot = data['directory']/'snapshot.json'
        if snapshot.is_file():
            try:
                self._inside(snapshot, data['directory'])
                if snapshot.stat().st_size > 65536:
                    raise ValueError('snapshot过大。')
                saved = json.loads(snapshot.read_text())
                pose = saved.get('map_pose')
                if not isinstance(pose, list) or len(pose) != 3:
                    raise ValueError('snapshot缺少有效map_pose。')
                pose = [_number(value, 'map_pose') for value in pose]
                if saved.get('frame_id', 'map') != 'map':
                    raise ValueError('保存pose不是map坐标。')
                dimensions = saved.get('map', {})
                if not isinstance(dimensions, dict):
                    raise ValueError('保存pose的地图信息无效。')
                if ('width' in dimensions and dimensions['width'] != data['width'] or
                        'height' in dimensions and dimensions['height'] != data['height']):
                    raise ValueError('保存pose的地图尺寸不一致。')
                if 'resolution' in dimensions and not math.isclose(float(dimensions['resolution']), data['resolution'], rel_tol=1e-5):
                    raise ValueError('保存pose的地图分辨率不一致。')
                column, row = world_to_pixel(pose[0], pose[1], data)
                if not data['free'][row,column]:
                    raise ValueError('保存pose不在已知自由空间。')
                if not data['labels'][row,column]:
                    raise ValueError('保存pose净空%.3fm不足%.1fm。' % (data['clearance'][row,column], CLEARANCE_M))
                return pose, int(data['labels'][row,column]), 'snapshot.map_pose', None
            except (ValueError, OSError, TypeError, AttributeError, KeyError) as exc:
                rejection = str(exc)[:180]
        stats = data['statistics']
        if len(stats) <= 1:
            raise ValueError('地图没有满足0.4米净空的已知自由区域。')
        label = 1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
        rows, columns = np.where(data['labels'] == label)
        center_x, center_y = data['centers'][label]
        index = int(np.argmin((columns-center_x)**2+(rows-center_y)**2))
        x, y = pixel_to_world(int(columns[index]), int(rows[index]), data)
        return [x,y,0.], label, 'largest_clearance_component_center', rejection

    @staticmethod
    def _preview_shape(data):
        scale = min(1., PREVIEW_MAX/max(data['width'], data['height']))
        return max(1,round(data['width']*scale)), max(1,round(data['height']*scale)), scale

    @staticmethod
    def _preview_pixel(x, y, data, width, height):
        ox, oy, yaw = data['origin']
        dx, dy = x-ox, y-oy
        local_x = math.cos(yaw)*dx+math.sin(yaw)*dy
        local_y = -math.sin(yaw)*dx+math.cos(yaw)*dy
        # Preserve subcell position of saved poses, including rotated origins.
        pixel_x = local_x/data['resolution']*width/data['width']-.5
        pixel_y = (data['height']-local_y/data['resolution'])*height/data['height']-.5
        return {'x':max(0.,min(width-1.,pixel_x)), 'y':max(0.,min(height-1.,pixel_y))}

    def _select(self, data, count, radius_m):
        pose, label, source, rejection = self._reference(data)
        rows, columns = np.where(data['labels'] == label)
        resolution, (ox, oy, yaw) = data['resolution'], data['origin']
        local_x, local_y = (columns+.5)*resolution, (data['height']-rows-.5)*resolution
        world = np.column_stack((ox+math.cos(yaw)*local_x-math.sin(yaw)*local_y,
                                 oy+math.sin(yaw)*local_x+math.cos(yaw)*local_y))
        squared = np.sum((world-np.asarray(pose[:2]))**2, axis=1)
        allowed = (squared <= radius_m*radius_m+1e-10)
        world, rows, columns, squared = world[allowed], rows[allowed], columns[allowed], squared[allowed]
        selected = []
        if len(world):
            separation = squared.copy()
            for _ in range(count):
                if selected:
                    nearest = np.min(np.sum((world[:,None,:]-world[np.asarray(selected)][None,:,:])**2, axis=2), axis=1)
                    separation = nearest
                    separation[nearest < MIN_SEPARATION_M**2] = -1
                if not len(separation) or float(separation.max()) < 0:
                    break
                index = int(np.argmax(separation))
                selected.append(index)
                separation[index] = -1
        if len(selected) != count:
            raise ValueError('该参考点半径内没有%d个分散且有0.4米净空的同区域观察点，请选择其他地图或减少数量。' % count)
        # Candidate order is deterministic; this is not an ordered path solution.
        return pose, source, rejection, [(float(world[i,0]),float(world[i,1]),int(columns[i]),int(rows[i])) for i in selected]

    def list_maps(self):
        items = []
        for map_id in self._registry():
            try:
                data = self._load(map_id)
                item = {key:data[key] for key in ('id','name','width','height','resolution','origin','modified_at','map_sha256')}
                item.update(available=True, known_free_cells=int(data['free'].sum()),
                            unknown_value_guard=205, threshold_mismatch=data['threshold_mismatch'])
                try:
                    self._select(data,3,2.)
                    item['suggestible'] = True
                except ValueError:
                    item['suggestible'] = False
            except ValueError as exc:
                item = {'id':map_id,'name':map_id,'available':False,'suggestible':False,
                        'modified_at':0.,'error':str(exc)}
            item['saved_date'] = None
            for date in reversed(re.findall(r'20\d{6}',map_id)):
                try:
                    item['saved_date'] = datetime.strptime(date,'%Y%m%d').strftime('%Y-%m-%d')
                    break
                except ValueError:
                    pass
            items.append(item)
        # Metadata repairs change mtime; saved capture date remains the priority.
        items.sort(key=lambda item: (item.get('saved_date') or '',item['modified_at'],item['id']), reverse=True)
        usable = next((item for item in items if item.get('suggestible')), None)
        return {'items':items, 'default_id':usable['id'] if usable else None,
                'live_localization':False, 'motion_enabled':False}

    def preview(self, map_id):
        data = self._load(map_id)
        image = np.full((data['height'],data['width'],3), 155, np.uint8)
        image[data['free']] = (242,242,242)
        image[data['occupied']] = (25,25,25)
        width, height, _ = self._preview_shape(data)
        image = cv2.resize(image,(width,height),interpolation=cv2.INTER_NEAREST)
        ok, encoded = cv2.imencode('.png',image)
        if not ok:
            raise ValueError('地图缩略图生成失败。')
        return encoded.tobytes()

    def suggest(self, map_id, count=3, radius_m=2):
        if isinstance(count,bool) or not isinstance(count,int) or not 1 <= count <= 12:
            raise ValueError('观察点数量须为1到12。')
        radius_m = _number(radius_m,'选择半径')
        if not .5 <= radius_m <= 5:
            raise ValueError('选择半径须为0.5到5米。')
        data = self._load(map_id)
        pose, source, rejection, chosen = self._select(data,count,radius_m)
        width, height, scale = self._preview_shape(data)
        points, pixels = [], []
        for index, (x,y,column,row) in enumerate(chosen,1):
            name = '观察点%d' % index
            yaw = math.atan2(pose[1]-y,pose[0]-x) if math.hypot(pose[0]-x,pose[1]-y)>1e-8 else pose[2]
            yaw = math.atan2(math.sin(yaw),math.cos(yaw))
            points.append({'name':name,'x':x,'y':y,'yaw':yaw})
            pixels.append(dict(self._preview_pixel(x,y,data,width,height),name=name))
        reference_pixel = self._preview_pixel(pose[0],pose[1],data,width,height)
        warning = '静态地图候选；参考点不是实时机器人位置，未验证依次通行路径或动态避障。'
        if data['threshold_mismatch']:
            warning += '旧YAML阈值会把灰度205未知区域读成自由区，本次已强制排除。'
        if rejection:
            warning += '保存参考位置不适用，已使用有净空区域中心。'
        return {'name':map_id[:24]+'观察路线','frame_id':'map','points':points,
            'generation':{'map_id':map_id,'map_sha256':data['map_sha256'],'origin':data['origin'],
                'resolution':data['resolution'],'reference_pose_source':source,'reference_pose':pose,
                'reference_rejected_reason':rejection,'live_localization':False,
                'method':'opencv_distance_transform_4_connected_farthest_sampling',
                'clearance_m':CLEARANCE_M,'minimum_separation_m':MIN_SEPARATION_M,'radius_m':radius_m,
                'unknown_value_guard':205,'threshold_mismatch':data['threshold_mismatch'],
                'ordered_route_validated':False,'width':width,'height':height,
                'point_pixels':pixels,'reference_pixel':reference_pixel,
                'preview':{'width':width,'height':height,'scale':scale}},
            'warning':warning,'motion_enabled':False,'physical_execution':False}
