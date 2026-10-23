"""Decoder boundaries for the two verified export formats; no GPU or robot I/O."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from helmet_detector import decode_predictions,model_spec


class DecoderTests(unittest.TestCase):
    def decode(self,rows):
        return decode_predictions(np.asarray([rows],np.float32),['Hardhat','NO-Hardhat'],
                                  'yolov5_objectness',(640,640,3),1,0,0)

    def test_objectness_multiplies_class_probability(self):
        self.assertEqual([],self.decode([[100,100,40,40,.1,.01,.99]]))
        result=self.decode([[100,100,40,40,.8,.1,.9]])
        self.assertEqual('NO-Hardhat',result[0]['label'])
        self.assertAlmostEqual(.72,result[0]['confidence'],places=6)

    def test_different_classes_keep_conflicting_evidence_for_rules(self):
        result=self.decode([[100,100,40,40,.9,.9,.1],[100,100,40,40,.9,.1,.9]])
        self.assertEqual({'Hardhat','NO-Hardhat'},{r['label'] for r in result})

    def test_letterbox_coordinates_and_classwise_nms(self):
        result=decode_predictions(np.asarray([[[320,320,160,80,.9,.9,.1],
                                               [321,321,160,80,.8,.9,.1]]],np.float32),
                                  ['Hardhat','NO-Hardhat'],'yolov5_objectness',(240,480,3),
                                  640/480,0,160)
        self.assertEqual(1,len(result))
        np.testing.assert_allclose([180,90,300,150],result[0]['bbox'],atol=1e-5)

    def test_yolo11_layout_is_preserved(self):
        output=np.zeros((1,7,1),np.float32);output[0,:,0]=[100,100,40,40,.1,.01,.9]
        result=decode_predictions(output,['Hardhat','Mask','NO-Hardhat'],
                                  'yolo11_class_scores',(640,640,3),1,0,0)
        self.assertEqual('NO-Hardhat',result[0]['label'])
        self.assertAlmostEqual(.9,result[0]['confidence'],places=6)

    def test_invalid_shape_and_nonfinite_values_rejected(self):
        for values in (np.zeros((1,8,3)),np.full((1,1,7),np.nan)):
            with self.assertRaises(ValueError):
                decode_predictions(values,['Hardhat','NO-Hardhat'],'yolov5_objectness',
                                   (640,640,3),1,0,0)

    def test_manifest_hash_and_mapping_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'helmet.onnx').write_bytes(b'not-loaded-by-this-test')
            spec={'model_file':'helmet.onnx','labels':['helmet','head'],
                  'class_map':{'helmet':'Hardhat','head':'NO-Hardhat'},
                  'format':'yolov5_objectness',
                  'model_sha256':hashlib.sha256((root/'helmet.onnx').read_bytes()).hexdigest()}
            path=root/'helmet.labels.json';path.write_text(json.dumps(spec))
            self.assertEqual(['Hardhat','NO-Hardhat'],model_spec(root)[3])
            spec['class_map']={'helmet':'NO-Hardhat','head':'Hardhat'}
            path.write_text(json.dumps(spec))
            with self.assertRaises(ValueError):model_spec(root)
            spec['class_map']={'helmet':'Hardhat','head':'NO-Hardhat'}
            spec['model_sha256']='0'*64;path.write_text(json.dumps(spec))
            with self.assertRaises(ValueError):model_spec(root)


if __name__=='__main__':unittest.main(verbosity=2)
