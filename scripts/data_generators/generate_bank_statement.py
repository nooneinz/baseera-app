import pandas as pd
import numpy as np
import random
from datetime import timedelta, date

np.random.seed(42)
data = []
start_date = date(2023, 1, 1)
for i in range(50):
    d = start_date + timedelta(days=random.randint(0, 180))
    amt = round(random.uniform(-500, 1500), 2)
    type_ = 'Expense' if amt < 0 else 'Income'
    desc = random.choice(['Rent', 'Salary', 'Supplies', 'Marketing', 'Utilities', 'Sales']) if amt < 0 else 'Sales Revenue'
    data.append({'Date': d.strftime('%Y-%m-%d'), 'Description': desc, 'Amount': abs(amt), 'Type': type_, 'Category': desc})

df = pd.DataFrame(data)
df.sort_values(by='Date', inplace=True)
df.to_csv('synthetic_bank_statement.csv', index=False)
print("Dataset generated: synthetic_bank_statement.csv")
