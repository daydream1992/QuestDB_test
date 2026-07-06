#!/bin/bash
# pre-commit hook: 全量 Python 语法检查
# 防止引入语法错误的文件（避免 P0 回归）
# 安装: cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

echo "🔍 pre-commit: 语法检查..."

ERRORS=0
for f in $(git diff --cached --name-only --diff-filter=ACM | grep '\.py$'); do
    if [ ! -f "$f" ]; then
        continue
    fi
    if python -c "
import ast, sys
try:
    with open('$f', 'r') as fh:
        ast.parse(fh.read())
except SyntaxError as e:
    print(f'  SYNTAX ERROR: $f:{e.lineno}: {e.msg}')
    sys.exit(1)
" 2>/dev/null; then
    :  # OK
else
    ERRORS=$((ERRORS + 1))
fi
done

if [ $ERRORS -gt 0 ]; then
    echo "❌ pre-commit: $ERRORS 个文件语法错误，拒绝提交"
    echo "   修复后重试，或用 git commit --no-verify 跳过"
    exit 1
fi

echo "✅ pre-commit: 语法检查通过"
exit 0
