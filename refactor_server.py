import re

with open('backend/server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Update base thresholds
content = content.replace(
    'base_thresholds = {1: 0.66, 3: 0.63, 5: 0.60, 7: 0.60, 10: 0.58, 15: 0.57}',
    'base_thresholds = {1: 0.70, 3: 0.68, 5: 0.64, 7: 0.63, 10: 0.61, 15: 0.60}'
)

# Explicit mapping to TRADE BUY, TRADE SELL, ABSTAIN
old_return = '''    prediction["requiredConfidence"] = round(threshold, 3)
    return prediction'''

new_return = '''    prediction["requiredConfidence"] = round(threshold, 3)
    
    # Map outputs to TRADE BUY, TRADE SELL, ABSTAIN
    if prediction.get("direction") == "UP":
        prediction["signal"] = "TRADE BUY"
    elif prediction.get("direction") == "DOWN":
        prediction["signal"] = "TRADE SELL"
    else:
        prediction["signal"] = "ABSTAIN"
        
    return prediction'''

content = content.replace(old_return, new_return)

with open('backend/server.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Refactored server.py successfully!')
