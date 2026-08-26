import pandas as pd
import numpy as np
import random
from datetime import timedelta, date

np.random.seed(100)

items = {
    "Panadol Extra 500mg": (1.200, "Painkillers"),
    "Brufen 400mg": (1.500, "Painkillers"),
    "Vitamin C 1000mg": (3.500, "Vitamins"),
    "Omega 3 Fish Oil": (6.000, "Vitamins"),
    "Amoxil 500mg (Antibiotic)": (4.200, "Prescription"),
    "Cough Syrup": (2.100, "Cold & Flu"),
    "Band-Aids": (0.800, "First Aid"),
    "Face Wash (Vichy)": (9.500, "Cosmetics"),
    "Sunblock (La Roche)": (12.000, "Cosmetics"),
    "Baby Formula Milk": (4.800, "Baby Care"),
    "Diapers (Pampers)": (6.500, "Baby Care"),
    "Blood Pressure Monitor": (15.000, "Medical Devices")
}

data = []
start_date = date(2023, 8, 1)

for i in range(150):
    d = start_date + timedelta(days=random.randint(0, 30))
    item, (price, category) = random.choice(list(items.items()))
    qty = random.randint(1, 5)
    
    # Introduce some logical anomalies for the AI to catch
    if item == "Sunblock (La Roche)" and d.day > 20:
        qty = random.randint(10, 15)  # Sudden spike in sales
    
    cost = price * 0.6  # 40% margin roughly
    if item == "Baby Formula Milk":
        cost = price * 0.85 # Low margin
        
    revenue = price * qty
    profit = revenue - (cost * qty)
    
    data.append({
        "التاريخ": d.strftime('%Y-%m-%d'),
        "الصنف": item,
        "التصنيف": category,
        "الكمية": qty,
        "سعر الوحدة (ر.ع)": round(price, 3),
        "التكلفة (ر.ع)": round(cost, 3),
        "إجمالي المبيعات (ر.ع)": round(revenue, 3),
        "الربح (ر.ع)": round(profit, 3)
    })

df = pd.DataFrame(data)
df.sort_values(by="التاريخ", inplace=True)
df.to_csv("pharmacy_sales.csv", index=False, encoding='utf-8-sig')
print("Pharmacy dataset generated.")
