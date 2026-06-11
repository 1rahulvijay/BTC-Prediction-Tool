import re

with open('src/main.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace 'BUY / UP' with 'TRADE BUY' and 'SELL / DOWN' with 'TRADE SELL' and 'AVOID / SKIP' with 'ABSTAIN'
content = content.replace("const action = dir === 'UP' ? 'BUY / UP' : dir === 'DOWN' ? 'SELL / DOWN' : 'AVOID / SKIP';", "const action = dir === 'UP' ? 'TRADE BUY' : dir === 'DOWN' ? 'TRADE SELL' : 'ABSTAIN';")

with open('src/main.js', 'w', encoding='utf-8') as f:
    f.write(content)
print('Refactored main.js successfully!')
