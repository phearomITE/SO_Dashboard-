import os,subprocess,tempfile
from pathlib import Path
from PIL import Image,ImageOps
import numpy as np
from app.rules import photo_status

def scan(path,submitted):
    from app.diagnostics import tesseract_command
    exe=tesseract_command()
    try:
        with Image.open(path) as image:
            image=ImageOps.exif_transpose(image).convert('RGB')
            w,h=image.size
            # Timestamp commonly appears along the lower edge; also scan top and full image.
            regions=[image.crop((0,int(h*.65),w,h)),image.crop((0,0,w,int(h*.35))),image]
            texts=[]
            with tempfile.TemporaryDirectory() as td:
                for i,im in enumerate(regions):
                    if im.width<1400:im=im.resize((im.width*2,im.height*2))
                    f=Path(td)/f'crop{i}.png';ImageOps.autocontrast(ImageOps.grayscale(im)).save(f)
                    p=subprocess.run([exe,str(f),'stdout','--psm','11','-l','eng'],capture_output=True,text=True,timeout=25,check=True)
                    texts.append(p.stdout)
                    result=photo_status(p.stdout,submitted)
                    if result.get('photo_time'):
                        result['ocr_text']=p.stdout[:4000];return result
                # Timemark's white name/time badge can split digits when the
                # entire photograph is OCR'd. Detect its horizontal white frame
                # and read the right-hand time area alone as a single line.
                pixels=np.asarray(image)
                white=np.min(pixels,axis=2)>220
                candidates=[]
                for y in range(h//2,h,2):
                    padded=np.r_[False,white[y],False].astype(int)
                    edges=np.diff(padded);starts=np.where(edges==1)[0];ends=np.where(edges==-1)[0]
                    for x1,x2 in zip(starts,ends):
                        if w*.25 < x2-x1 < w*.9:
                            if not candidates or abs(y-candidates[-1][0])>h*.08:
                                candidates.append((y,int(x1),int(x2)))
                for i,(y,x1,x2) in enumerate(candidates[:4]):
                    crop=image.crop((int(x1+(x2-x1)*.67),max(0,y-4),min(w,x2+int(w*.09)),min(h,y+int(h*.055))))
                    crop=crop.resize((crop.width*3,crop.height*3))
                    f=Path(td)/f'badge{i}.png';crop.save(f)
                    p=subprocess.run([exe,str(f),'stdout','--psm','7','-l','eng'],capture_output=True,text=True,timeout=15,check=True)
                    result=photo_status(p.stdout+'\n'+texts[0],submitted)
                    if result.get('photo_time'):
                        result['ocr_text']=(p.stdout+'\n'+texts[0])[:4000];return result
            return {**photo_status('\n'.join(texts),submitted),'ocr_text':'\n'.join(texts)[:4000]}
    except (OSError,subprocess.SubprocessError,ValueError) as e:
        return {'status':'Review','gap':None,'photo_time':'','ocr_issue':f'OCR failed: {type(e).__name__}','ocr_text':''}
