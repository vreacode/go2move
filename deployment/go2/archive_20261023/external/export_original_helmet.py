#!/usr/bin/env python3
"""Export the user's archived YOLOv5 model with existing original YOLOv5 sources.
Run only in helmet_export_venv. Production remains torch-free.
"""
import hashlib,json,os,pathlib,sys,time
from pathlib import Path
os.environ['YOLOv5_AUTOINSTALL']='false'
os.environ['MPLCONFIGDIR']='/tmp/go2-helmet-export-mpl'
sys.path.insert(0,'/home/unitree/go2move/apps/nano-deploy')
sys.path.insert(0,'/home/unitree/go2move/deployment/go2')
from go2_platform_write import backup,write
import cv2,numpy as np,torch,onnx
root=Path('/home/unitree/go2_ros2_ws/models/helmet_custom')
pt=root/'helmet.pt'
assert hashlib.sha256(pt.read_bytes()).hexdigest()=='aa1563af00d971e4e6d8087b0d36750d2fb4c9f04e7cb1f3129fad0e3c91d806'
# The user trained on Windows. Normalize only this export process's unpickling.
pathlib.WindowsPath=pathlib.PosixPath
torch.set_num_threads(2)
checkpoint=torch.load(pt,map_location='cpu',weights_only=False)
model=(checkpoint.get('ema') or checkpoint['model']).float().eval()
names=model.names
labels=[names[i] for i in range(len(names))] if isinstance(names,dict) else list(names)
assert labels==['helmet','head'],labels
print('original labels',labels,'training source',checkpoint.get('date'),flush=True)
for module in model.modules():
 if module.__class__.__name__=='Detect':
  module.inplace=False;module.dynamic=False;module.export=True
model=model.fuse().eval()
image=cv2.imread('/home/unitree/go2_ros2_ws/validation/helmet_demo_20261023/shwd_2.jpg')
h,w=image.shape[:2];ratio=min(640/w,640/h);nw,nh=round(w*ratio),round(h*ratio)
left,top=(640-nw)//2,(640-nh)//2
canvas=np.full((640,640,3),114,np.uint8);canvas[top:top+nh,left:left+nw]=cv2.resize(image,(nw,nh))
x=torch.from_numpy(np.ascontiguousarray(canvas[:,:,::-1].transpose(2,0,1)[None])).float()/255
with torch.inference_mode():
 pred=model(x);pred=pred[0] if isinstance(pred,(tuple,list)) else pred
print('PyTorch output',tuple(pred.shape),flush=True)
assert tuple(pred.shape)==(1,25200,7)
npz=root/'export_reference.npz';backup(npz);np.savez(npz,input=x.numpy(),output=pred.numpy(),image_shape=[h,w])
output=root/'helmet.onnx';backup(output)
torch.onnx.export(model,x,str(output),input_names=['images'],output_names=['output'],opset_version=12,do_constant_folding=True)
graph=onnx.load(str(output));onnx.checker.check_model(graph)
for key,value in {'names':json.dumps(dict(enumerate(labels))),'source_checkpoint_sha256':hashlib.sha256(pt.read_bytes()).hexdigest(),'output_layout':'yolov5_objectness'}.items():
 prop=graph.metadata_props.add();prop.key=key;prop.value=value
onnx.save(graph,str(output))
metadata={'model_file':output.name,'model_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
 'labels':labels,'class_map':{'helmet':'Hardhat','head':'NO-Hardhat'},'format':'yolov5_objectness',
 'source':'original_trained_helmet','source_checkpoint':str(pt),'source_checkpoint_sha256':hashlib.sha256(pt.read_bytes()).hexdigest(),
 'backend':'TensorRT FP16 / original YOLOv5 helmet','input_shape':[1,3,640,640],'output_shape':[1,25200,7],
 'checkpoint_date':str(checkpoint.get('date')),'note':'head is the original training dataset bare-head class, not absence of a helmet detection'}
write(root/'helmet.labels.json',json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
print('exported',str(output),output.stat().st_size,'sha256',metadata['model_sha256'],flush=True)
