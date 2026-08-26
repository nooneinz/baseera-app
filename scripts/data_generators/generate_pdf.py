import os
import random
from datetime import timedelta, date
import sys
import subprocess

try:
    from fpdf import FPDF
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf"])
    from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(80)
        self.cell(30, 10, 'Pharmacy Bank Statement', 0, 0, 'C')
        self.ln(20)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

pdf = PDF()
pdf.add_page()
pdf.set_font('Arial', '', 12)

# Column headers
pdf.set_font('Arial', 'B', 12)
pdf.cell(40, 10, 'Date', 1)
pdf.cell(70, 10, 'Description', 1)
pdf.cell(30, 10, 'Amount (OMR)', 1)
pdf.cell(30, 10, 'Type', 1)
pdf.ln(10)

pdf.set_font('Arial', '', 12)

start_date = date(2023, 1, 1)
descriptions = [
    ("Rent Payment", "Expense"),
    ("Salary - Pharmacist", "Expense"),
    ("Medical Supplies (Vendor A)", "Expense"),
    ("Daily Sales Deposit", "Income"),
    ("Daily Sales Deposit", "Income"),
    ("Electricity Bill", "Expense"),
    ("Internet Bill", "Expense"),
    ("Daily Sales Deposit", "Income")
]

random.seed(42)

for i in range(40):
    d = start_date + timedelta(days=random.randint(0, 300))
    desc, ttype = random.choice(descriptions)
    
    if ttype == "Income":
        amt = round(random.uniform(100, 800), 2)
    else:
        amt = round(random.uniform(50, 400), 2)
        if "Rent" in desc:
            amt = 500.00
            
    pdf.cell(40, 10, d.strftime('%Y-%m-%d'), 1)
    pdf.cell(70, 10, desc, 1)
    pdf.cell(30, 10, str(amt), 1)
    pdf.cell(30, 10, ttype, 1)
    pdf.ln(10)

file_path = "pharmacy_bank_statement.pdf"
pdf.output(file_path)
print(f"PDF generated successfully at {os.path.abspath(file_path)}")
