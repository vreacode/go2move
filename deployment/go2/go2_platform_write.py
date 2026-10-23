from pathlib import Path
import shutil,time,json
BACKUP=Path('/home/unitree/go2_ros2_ws/backups/platform-20261005-194025')
BACKUP.mkdir(parents=True,exist_ok=True)
def backup(path):
 p=Path(path);stamp=str(time.time_ns());dest=BACKUP/'revisions'/stamp/str(p).lstrip('/')
 dest.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():
  if p.is_dir():shutil.copytree(p,dest,symlinks=True)
  else:shutil.copy2(p,dest)
 else:dest.with_name(dest.name+'.absent').write_text('New path; did not exist before this change.\n')
 with (BACKUP/'changes.jsonl').open('a') as f:f.write(json.dumps({'time_ns':stamp,'path':str(p),'backup':str(dest),'existed':p.exists()})+'\n')
def write(path,content,mode=None):
 p=Path(path);backup(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
 if mode is not None:p.chmod(mode)
