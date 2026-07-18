# Combine Multispectral Images from single-band Altum multispectral images

## Uses
This specific program is used to combine different multispectral images from a Micasense Altum multispectral sensor where Band 1 is blue, Band 2 is green and Band 3 is red. 
I used this by downloading the required images onto onedrive and then downloaded them to my personal laptop. I then ran the converting code after I identified the best reflectance panel calibration images.


##environment needs to include
Conda install argparse, gc, re, pathlib, cv2, numpy, tifffile, PIL import Image, ImageDraw


## How to Use
This the command used to run the code

& 'D:\Conda\envs\ML\python.exe' 'D:\Play\Combine Multi\scripts\create_altum_rgb.py' ` 
  --input '' `
  --output '' `
  --panel-stems IMG_0000 IMG_0001 `
  --manual-panel-roi 1155 535 1285 675 ` "LEFT", "TOP", "RIGHT", "BOTTOM" used w
  --exclude-stems IMG_0000 IMG_0001 `
  --preview
