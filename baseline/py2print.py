import re,sys
# Environment shim only: the repo's generated script is Python 2; convert its print *statements* to print().
out=[]
for line in open(sys.argv[1]).read().split('\n'):
    m=re.match(r"^(\s*(?:if [^:]+:\s*)?)print\s+(.*?)(,?)\s*$", line)
    if m:
        pre,arg,comma=m.groups(); line=f"{pre}print({arg}{', end=\" \"' if comma else ''})"
    out.append(line)
open(sys.argv[2],'w').write('\n'.join(out))
