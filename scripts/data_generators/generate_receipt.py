from PIL import Image, ImageDraw, ImageFont
import os

img = Image.new('RGB', (400, 600), color=(255, 255, 255))
d = ImageDraw.Draw(img)

try:
    font = ImageFont.truetype("arial.ttf", 20)
    font_bold = ImageFont.truetype("arialbd.ttf", 24)
except IOError:
    font = ImageFont.load_default()
    font_bold = font

text = """
       BASEERA PHARMACY
       Muscat, Oman
       Tel: +968 1234 5678
------------------------------------
Date: 2026-08-15  Time: 14:30
------------------------------------
Item                    Qty    Total
------------------------------------
Panadol Extra 500mg      2      2.400
Vitamin C 1000mg         1      3.500
Face Wash (Vichy)        1      9.500
Band-Aids                3      2.400
------------------------------------
Subtotal:                      17.800
VAT (5%):                       0.890
------------------------------------
TOTAL (OMR):                   18.690
------------------------------------
Payment: CARD
      THANK YOU FOR VISITING!
"""

d.text((20, 20), text, fill=(0, 0, 0), font=font)
img.save(r"C:\Users\saraa\Desktop\Baseera_Test_Data\5_Receipt_OCR.png")
print("Receipt image generated.")
