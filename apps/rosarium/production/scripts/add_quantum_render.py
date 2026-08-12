import re

html_file = "/home/ubuntu/arcane-princess-studio/index.html"
with open(html_file, "r") as f:
    content = f.read()

# Add quantum_leap_masterpiece.png to seed42 collection
target = '{ src: "/renders/seed42_throne_room.png", tag: "SEED 42 • CRYSTAL THRONE" },'
replacement = '{ src: "/renders/quantum_leap_masterpiece.png", tag: "QUANTUM LEAP • MASTERPIECE" },\n                ' + target

content = content.replace(target, replacement)

with open(html_file, "w") as f:
    f.write(content)

print("[+] Added quantum_leap_masterpiece.png to seed42 collection!")
