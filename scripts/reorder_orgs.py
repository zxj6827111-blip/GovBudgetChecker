import csv
import json
from pathlib import Path

csv_path = Path(r'd:\软件开发\TRAE\GovBudgetChecker\data\组织架构导入模板.csv')
json_path = Path(r'd:\软件开发\TRAE\GovBudgetChecker\data\organizations.json')

# 1. Read CSV and keep the order
order_dict = {}
index = 0
with open(csv_path, 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    next(reader, None) # skip header
    for row in reader:
        if not row: continue
        name = row[0].strip()
        if name and name not in order_dict:
            order_dict[name] = index
            index += 1

# 2. Read JSON
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

orgs = data.get('organizations', [])

# 3. Sort orgs based on the order_dict.
# If an org name is not in the CSV, we'll put it at the end.
def sort_key(org):
    name = org.get('name', '').strip()
    return order_dict.get(name, 999999)

orgs.sort(key=sort_key)

data['organizations'] = orgs

# 4. Save JSON
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f'Successfully reordered {len(orgs)} organizations based on template order.')

# For verification:
for org in orgs[:10]:
    print(org['name'])
