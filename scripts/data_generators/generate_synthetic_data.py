import pandas as pd
import numpy as np
import random
from datetime import timedelta, date

np.random.seed(42)

start_date = date(2023, 1, 1)
num_records = 2000

categories = ["Laptops", "Smartphones", "Accessories", "Tablets", "Wearables"]
branches = ["Muscat", "Salalah", "Sohar", "Nizwa"]
channels = ["Retail", "Online", "Wholesale"]

data = []

for i in range(num_records):
    days_offset = random.randint(0, 360)
    record_date = start_date + timedelta(days=days_offset)
    month = record_date.month
    
    category = random.choices(categories, weights=[0.2, 0.35, 0.25, 0.1, 0.1])[0]
    branch = random.choices(branches, weights=[0.4, 0.25, 0.2, 0.15])[0]
    channel = random.choices(channels, weights=[0.5, 0.4, 0.1])[0]
    
    base_price = {"Laptops": 350, "Smartphones": 250, "Accessories": 25, "Tablets": 150, "Wearables": 80}[category]
    price = base_price * random.uniform(0.9, 1.1)
    cost = price * random.uniform(0.5, 0.7)
    qty = random.randint(1, 5)
    
    if channel == "Wholesale":
        qty *= random.randint(10, 20)
        price *= 0.8
    
    if branch == "Salalah" and month == 8:
        if random.random() < 0.8:
            continue
            
    status = "Completed"
    if category == "Smartphones" and month == 10:
        if random.random() < 0.4:
            status = "Returned"
    else:
        if random.random() < 0.05:
            status = "Returned"
            
    waste = 0
    if status == "Returned":
        waste = cost * qty * random.uniform(0.2, 1.0)
        
    revenue = price * qty if status == "Completed" else 0
    profit = (price - cost) * qty if status == "Completed" else -waste
    
    data.append({
        "Date": record_date.strftime("%Y-%m-%d"),
        "Branch": branch,
        "Channel": channel,
        "Category": category,
        "Quantity": qty,
        "Unit Price": round(price, 2),
        "Cost": round(cost, 2),
        "Total Sales": round(price * qty, 2),
        "Revenue": round(revenue, 2),
        "Profit": round(profit, 2),
        "Waste": round(waste, 2),
        "Status": status
    })

df = pd.DataFrame(data)
df.sort_values(by="Date", inplace=True)
df.to_csv("synthetic_business_data.csv", index=False)
print("Dataset generated: synthetic_business_data.csv")
