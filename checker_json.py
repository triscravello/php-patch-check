import json

from checker import collect_reports

reports = collect_reports()
print(json.dumps(reports, indent=2))