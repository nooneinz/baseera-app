import os
import csv
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont
import datetime

# Setup Desktop Path
desktop_path = os.path.join(os.environ['USERPROFILE'], 'Desktop', 'Baseera_Supermarket_Data')
os.makedirs(desktop_path, exist_ok=True)

# 1. Create CSV File (Audit Agent / Waste Discovery)
csv_path = os.path.join(desktop_path, 'inventory_data.csv')
with open(csv_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['Product ID', 'Product Name', 'Category', 'Stock Quantity', 'Daily Sales Avg', 'Purchase Price (OMR)', 'Selling Price (OMR)', 'Expiry Date', 'Status'])
    
    # Critical overstock item expiring in 3 days
    expiry_date = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime('%Y-%m-%d')
    writer.writerow(['101', '·»‰ ÿ«“Ã 1 · — (Fresh Yogurt 1L)', 'Dairy', '5000', '50', '1.200', '1.800', expiry_date, 'Critical Overstock'])
    
    # Normal items
    normal_expiry = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
    writer.writerow(['102', 'Õ·Ì» ﬂ«„· «·œ”„ 2 · — (Milk 2L)', 'Dairy', '150', '40', '1.500', '2.100', normal_expiry, 'Normal'])
    writer.writerow(['103', ' ›«Õ 1 ﬂÃ„ (Apples 1kg)', 'Produce', '300', '100', '0.800', '1.500', normal_expiry, 'Normal'])
    writer.writerow(['104', '√—“ »”„ Ì 5 ﬂÃ„ (Basmati Rice 5kg)', 'Grocery', '400', '20', '3.500', '5.000', '2027-12-01', 'Normal'])

# 2. Create TXT File (Supply Chain Agent)
txt_path = os.path.join(desktop_path, 'warehouse_report.txt')
txt_content = """ ﬁ—Ì— √„Ì‰ «·„” Êœ⁄ (≈œ«—… ”·«”· «·≈„œ«œ):
«· «—ÌŒ: «·ÌÊ„
«·„Ê÷Ê⁄:  ﬂœ” ‘œÌœ ›Ì „Œ“Ê‰ «·√·»«‰ «·ÿ«“Ã…!

≈·Ï ›—Ìﬁ «·≈œ«—…°
»‰«¡ ⁄·Ï «·Ã—œ «·√ŒÌ— ··„Œ“Ê‰° «ﬂ ‘›‰« ÊÃÊœ  ﬂœ” Â«∆· ›Ì „‰ Ã "«··»‰ «·ÿ«“Ã 1 · —" »ﬂ„Ì…  »·€ 5000 ⁄»Ê….
«·”»» Ì⁄Êœ ≈·Ï Œÿ√ ›Ì ‰Ÿ«„ «·ÿ·» «·¬·Ì „⁄ «·„Ê—œ ÕÌÀ  „  ﬂ—«— «·ÿ·»Ì… ⁄œ… „—« .

«·„‘ﬂ·… «·ﬂ»—Ï ÂÌ √‰ ’·«ÕÌ… Â–Â «·„‰ Ã«   ‰ ÂÌ »⁄œ 3 √Ì«„ ›ﬁÿ!
ÌÃ» «· œŒ· «·”—Ì⁄ „‰ ›—Ìﬁ «· ”⁄Ì— · ’—Ì› Â–« «·„‰ Ã «·—«ﬂœ („À·« ⁄»— ⁄—Ê÷  —ÊÌÃÌ… ﬁÊÌ… 1+1 „Ã«‰« √Ê Œ’„ 50%) · Ã‰» Âœ— „«·Ì ÷Œ„.
"""
with open(txt_path, 'w', encoding='utf-8') as f:
    f.write(txt_content)

# 3. Create PDF File (Financial Agent)
pdf_path = os.path.join(desktop_path, 'financial_summary.pdf')
pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", 'B', 16)
pdf.cell(200, 10, txt="Financial & Liquidity Report - Supermarket HQ", ln=True, align='C')
pdf.ln(10)
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 10, txt="Date: Current Month\n\n"
                          "LIQUIDITY WARNING:\n"
                          "Our cash flow and liquidity this month are extremely tight due to recent store expansions.\n\n"
                          "PROFITABILITY IMPACT OF INVENTORY WASTE:\n"
                          "Any waste in perishable goods (especially high-volume dairy products) will severely impact our net margins. "
                          "If the 5000 units of yogurt expire, we will face a direct loss of 6,000 OMR (Purchase cost), "
                          "which represents 15% of our monthly net profit.\n\n"
                          "FINANCIAL AGENT RECOMMENDATION:\n"
                          "We must accept a lower margin to clear the stock. A discount of up to 40% on selling price "
                          "is financially viable and better than a 100% loss due to expiry. Implement clearance pricing immediately."
              )
pdf.output(pdf_path)

# 4. Create PNG File (Visual Alert for Pricing Agent / Dashboard)
png_path = os.path.join(desktop_path, 'stock_alert.png')
img = Image.new('RGB', (800, 400), color = (200, 30, 30))
d = ImageDraw.Draw(img)

# Try to use a basic font
try:
    font = ImageFont.truetype("arial.ttf", 36)
    font_small = ImageFont.truetype("arial.ttf", 24)
except IOError:
    font = ImageFont.load_default()
    font_small = font

d.text((50,50), "URGENT SYSTEM ALERT", fill=(255,255,255), font=font)
d.text((50,150), "Item: Fresh Yogurt 1L (ID: 101)", fill=(255,255,255), font=font_small)
d.text((50,200), "Status: 5000 Units Expiring in 3 Days!", fill=(255,255,255), font=font_small)
d.text((50,250), "Action Required: Immediate Clearance Pricing Strategy", fill=(255,200,50), font=font_small)
d.text((50,300), "Financial Impact: High Risk of 6000 OMR Loss", fill=(255,255,255), font=font_small)

img.save(png_path)

print(f"Data successfully generated in {desktop_path}")
