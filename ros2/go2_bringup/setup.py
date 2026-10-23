from setuptools import setup
from pathlib import Path
data=[('share/ament_index/resource_index/packages',['resource/go2_bringup']),('share/go2_bringup',['package.xml'])]
for folder in ['launch','config','urdf','rviz','vendor']:
 for p in Path(folder).rglob('*'):
  if p.is_file(): data.append(('share/go2_bringup/'+str(p.parent),[str(p)]))
setup(name='go2_bringup',version='0.1.0',packages=['go2_bringup'],data_files=data,
 install_requires=['setuptools'],zip_safe=False,
 entry_points={'console_scripts':['state_bridge=go2_bringup.state_bridge:main']})
