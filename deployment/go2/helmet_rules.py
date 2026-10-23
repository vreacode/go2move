"""Per-person head association and temporal evidence; no model or robot I/O."""
import math


def area(box):
    return max(0,box[2]-box[0])*max(0,box[3]-box[1])


def intersection(a,b):
    return area([max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])])


def iou(a,b):
    common=intersection(a,b)
    return common/max(1e-9,area(a)+area(b)-common)


def associate_heads(people, detections):
    """A head can belong to at most one body. Ambiguous overlaps stay unknown.

    Missing helmet detections are NOT no-helmet evidence. A positive NO-Hardhat
    class associated with the upper body is required.
    """
    assigned={p['track_id']:[] for p in people}
    for detection in detections:
        if detection['label'] not in ('Hardhat','NO-Hardhat') or detection['confidence']<.50:
            continue
        box=detection['bbox'];center=((box[0]+box[2])/2,(box[1]+box[3])/2)
        eligible=[]
        for p in people:
            x1,y1,x2,y2=p['bbox'];w,h=x2-x1,y2-y1
            zone=[x1-.05*w,y1-.05*h,x2+.05*w,y1+.40*h]
            fraction=intersection(box,zone)/max(area(box),1e-9)
            if (zone[0]<=center[0]<=zone[2] and zone[1]<=center[1]<=zone[3]
                    and fraction>=.65 and box[3]-box[1] <= .5*h):
                eligible.append((fraction,p['track_id']))
        eligible.sort(reverse=True)
        if not eligible or len(eligible)>1 and eligible[0][0]-eligible[1][0]<.15:
            continue
        assigned[eligible[0][1]].append(detection)
    result={}
    for track,heads in assigned.items():
        scores={label:max([d['confidence'] for d in heads if d['label']==label],default=0.)
                for label in ('Hardhat','NO-Hardhat')}
        if not heads or min(scores.values())>0 and abs(scores['Hardhat']-scores['NO-Hardhat'])<.15:
            result[track]={'raw_state':'unknown','confidence':None,'head_bbox':None}
        else:
            best=max(heads,key=lambda d:d['confidence'])
            result[track]={'raw_state':'helmet' if best['label']=='Hardhat' else 'no_helmet',
                           'confidence':best['confidence'],'head_bbox':best['bbox']}
    return result


class HelmetRules:
    def __init__(self):
        self.tracks={};self.last_stamp=None

    def update(self, people, detections, stamp, now):
        if (not math.isfinite(stamp) or not math.isfinite(now) or not 0<=now-stamp<=2
                or self.last_stamp is not None and stamp<=self.last_stamp):
            raise ValueError('安全帽观测已过期、重复或时间回退。')
        self.last_stamp=stamp
        associations=associate_heads(people,detections)
        output=[]
        visible={p['track_id'] for p in people}
        for track,entry in self.tracks.items():
            if track not in visible:
                entry['hits']=0;entry['raw_state']='unknown'
        self.tracks={k:v for k,v in self.tracks.items() if stamp-v['seen_at']<=10}
        for p in people:
            track=p['track_id'];observation=associations[track]
            previous=self.tracks.get(track)
            raw=observation['raw_state']
            continuous=previous and 0<stamp-previous['seen_at']<=1.5 and previous['raw_state']==raw
            hits=previous['hits']+1 if continuous else 1
            state=raw if raw=='helmet' or raw=='no_helmet' and hits>=3 else 'unknown'
            x1,y1,x2,y2=p['bbox']
            trail=((previous or {}).get('trail',[])+[[(x1+x2)/2,(y1+y2)/2]])[-20:]
            entry={'track_id':track,'bbox':p['bbox'],'state':state,'hits':hits,
                   'raw_state':raw,'seen_at':stamp,'trail':trail,**observation}
            self.tracks[track]=entry
            output.append(dict(entry,confirmed_violation=state=='no_helmet'))
        return output
