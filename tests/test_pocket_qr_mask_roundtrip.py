"""Mask recovery preserves binary payload and fails closed."""
import json
from pathlib import Path
import cv2
import qrcode
import pytest
import numpy as np
from PIL import Image
from pipelines import pocket_qr as qr

def payload():
    return qr.encode(list(range(1,21)),[],[1])

def test_automatic_mask_bytes_unchanged(tmp_path):
    code=payload()
    original=tmp_path/"original.png"
    obj=qrcode.QRCode(version=9,error_correction=qrcode.constants.ERROR_CORRECT_H,box_size=10,border=4)
    obj.add_data(code);obj.make(fit=False)
    obj.make_image(fill_color="black",back_color="white").save(original)
    assert cv2.QRCodeDetector().detectAndDecode(np.asarray(Image.open(original).convert("L")))[0]==code
    candidate=tmp_path/"candidate.png"
    assert qr.write_png(candidate,code)==qr.sha(original.read_bytes())
    assert candidate.read_bytes()==original.read_bytes()

def test_empty_read_tries_deterministic_mask_and_real_pixels(tmp_path,monkeypatch):
    code=payload();real=cv2.QRCodeDetector;ctor=qrcode.QRCode;seen=[]
    def render(*a,**k):
        seen.append(k.get("mask_pattern"));return ctor(*a,**k)
    class Reader:
        def detectAndDecode(self,pixels):
            if len(seen)==1:return "",None,None
            return real().detectAndDecode(pixels)
    monkeypatch.setattr(qrcode,"QRCode",render)
    monkeypatch.setattr(cv2,"QRCodeDetector",Reader)
    p=tmp_path/"recovered.png"
    qr.write_png(p,code)
    assert seen[0] is None and seen[1:]==list(range(len(seen)-1))
    assert real().detectAndDecode(np.asarray(Image.open(p).convert("L")))[0]==code

@pytest.mark.parametrize("wrong",[False,True])
def test_no_bypass(tmp_path,monkeypatch,wrong):
    code=payload();ctor=qrcode.QRCode;seen=[]
    def render(*a,**k):
        seen.append(k.get("mask_pattern"));return ctor(*a,**k)
    class Reader:
        def detectAndDecode(self,pixels):
            return (qr.encode(list(range(1,21)),[],[2]) if wrong else ""),None,None
    monkeypatch.setattr(qrcode,"QRCode",render)
    monkeypatch.setattr(cv2,"QRCodeDetector",Reader)
    with pytest.raises(ValueError,match="PNG decode mismatch"):
        qr.write_png(tmp_path/"bad.png",code)
    assert seen==([None] if wrong else [None,*range(8)])
