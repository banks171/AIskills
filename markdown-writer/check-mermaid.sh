#!/bin/bash
# Mermaid 图表错误检查脚本
# 检查会导致 VSCode 预览渲染失败的常见问题

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "用法: $0 <markdown文件>"
    echo "检查 Markdown 文件中的 Mermaid 图表渲染错误"
    exit 1
}

[ $# -eq 0 ] && usage
FILE="$1"
[ ! -f "$FILE" ] && echo -e "${RED}错误：文件不存在${NC}" && exit 1

echo -e "${YELLOW}检查 Mermaid 图表: $FILE${NC}\n"

ERRORS=0
WARNINGS=0
BLOCK_NUM=0
IN_MERMAID=0
CONTENT=""

while IFS= read -r line || [ -n "$line" ]; do
    # 检测 Mermaid 块开始
    if [[ "$line" =~ ^\`\`\`mermaid ]]; then
        IN_MERMAID=1
        BLOCK_NUM=$((BLOCK_NUM + 1))
        CONTENT=""
        echo -e "${YELLOW}[图表 #$BLOCK_NUM]${NC}"
        continue
    fi

    # 检测块结束
    if [[ "$line" =~ ^\`\`\`$ ]] && [ $IN_MERMAID -eq 1 ]; then
        # 执行检查

        # 检查 1: style 样式（致命错误）
        if echo "$CONTENT" | grep -q 'style.*fill:'; then
            echo -e "${RED}  ✗ 致命错误：包含禁止的 style 样式${NC}"
            echo "$CONTENT" | grep 'style.*fill:' | while IFS= read -r style_line; do
                echo -e "${RED}    $style_line${NC}"
            done
            ERRORS=$((ERRORS + 1))
        else
            echo -e "${GREEN}  ✓ 无禁止的 style 样式${NC}"
        fi

        # 检查 2: 图表类型
        if echo "$CONTENT" | grep -qE '(graph|flowchart|sequenceDiagram|timeline)'; then
            GRAPH_TYPE=$(echo "$CONTENT" | grep -oE '(graph|flowchart|sequenceDiagram|timeline)' | head -1)
            echo -e "${GREEN}  ✓ 图表类型：$GRAPH_TYPE${NC}"

            # Timeline 特殊检查：不应使用换行符分隔事件
            if [ "$GRAPH_TYPE" = "timeline" ]; then
                # 检查是否有以空格+冒号开头的行（表示换行分隔事件）
                NEWLINE_EVENTS=$(echo "$CONTENT" | grep -E '^\s+:' || true)
                if [ -n "$NEWLINE_EVENTS" ]; then
                    echo -e "${YELLOW}  ⚠ Timeline 警告：使用了换行符分隔事件${NC}"
                    echo -e "${YELLOW}    建议：将同一时间段的事件用冒号连接在同一行${NC}"
                    WARNINGS=$((WARNINGS + 1))
                else
                    echo -e "${GREEN}  ✓ Timeline 格式正确（同行冒号分隔）${NC}"
                fi
            fi
        fi

        # 检查 3: 节点名称长度（警告） - 使用字符数而非字节数
        HAS_LONG_NODE=0
        while IFS= read -r node_line; do
            if [[ "$node_line" =~ \[([^\]]+)\] ]]; then
                NODE_TEXT="${BASH_REMATCH[1]}"
                # 计算实际字符数（包括中文）
                CHAR_COUNT=$(echo -n "$NODE_TEXT" | wc -m)
                if [ "$CHAR_COUNT" -gt 15 ]; then
                    if [ $HAS_LONG_NODE -eq 0 ]; then
                        echo -e "${YELLOW}  ⚠ 警告：发现长节点名称（>15字符）${NC}"
                        HAS_LONG_NODE=1
                        WARNINGS=$((WARNINGS + 1))
                    fi
                    echo -e "${YELLOW}    [$NODE_TEXT] ($CHAR_COUNT字符)${NC}"
                fi
            fi
        done < <(echo "$CONTENT")

        if [ $HAS_LONG_NODE -eq 0 ]; then
            echo -e "${GREEN}  ✓ 节点名称长度合理${NC}"
        fi

        # 检查 4: 括号配对（致命错误）
        OPEN_BRACKETS=$(echo "$CONTENT" | grep -o '\[' | wc -l)
        CLOSE_BRACKETS=$(echo "$CONTENT" | grep -o '\]' | wc -l)
        if [ "$OPEN_BRACKETS" -ne "$CLOSE_BRACKETS" ]; then
            echo -e "${RED}  ✗ 错误：方括号不配对 ([ :$OPEN_BRACKETS, ] :$CLOSE_BRACKETS)${NC}"
            ERRORS=$((ERRORS + 1))
        fi

        # 检查 5: 特殊字符问题（可能导致解析错误）
        CHAR_ISSUES=0

        # 检查未保护的括号 - 排除已经用引号包围的情况
        if echo "$CONTENT" | grep -E '\[[^\]\"]*\([^\)]*\)[^\]\"]*\]' | grep -vE '\[\"[^\]]*\]' > /dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠ 特殊字符警告：节点中包含未保护的括号 ()${NC}"
            echo -e "${YELLOW}    建议：使用双引号包围节点 [\"节点名\"] 或改用冒号 VAR lag:1-4${NC}"
            CHAR_ISSUES=$((CHAR_ISSUES + 1))
            WARNINGS=$((WARNINGS + 1))
        fi

        # 检查嵌套的方括号
        if echo "$CONTENT" | grep -E '\[[^\]]*\[[^\]]*\]' > /dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠ 特殊字符警告：发现嵌套方括号${NC}"
            echo -e "${YELLOW}    建议：用点号替代嵌套括号 array[0] → array.0${NC}"
            CHAR_ISSUES=$((CHAR_ISSUES + 1))
            WARNINGS=$((WARNINGS + 1))
        fi

        # 检查节点中的花括号（不是判断节点的情况）
        if echo "$CONTENT" | grep -E '\[[^\]]*\{[^\}]*\}[^\]]*\]' | grep -vE '^\s*\w+\{' > /dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠ 特殊字符警告：普通节点中包含花括号 {}${NC}"
            echo -e "${YELLOW}    建议：花括号仅用于判断节点，如 C{判断}${NC}"
            CHAR_ISSUES=$((CHAR_ISSUES + 1))
            WARNINGS=$((WARNINGS + 1))
        fi

        # 检查HTML标签
        if echo "$CONTENT" | grep -E '\[[^\]]*<[^>]*>[^\]]*\]' > /dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠ 特殊字符警告：节点中包含HTML标签 <>${NC}"
            echo -e "${YELLOW}    建议：避免使用尖括号，改用文字描述${NC}"
            CHAR_ISSUES=$((CHAR_ISSUES + 1))
            WARNINGS=$((WARNINGS + 1))
        fi

        # 检查管道符冲突（连线标注中多个管道符）
        if echo "$CONTENT" | grep -E '--[>-]\|[^\|]*\|[^\|]*\|' > /dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠ 特殊字符警告：连线标注中管道符过多${NC}"
            echo -e "${YELLOW}    建议：标注内容用引号包围 -->|\"标注内容\"|${NC}"
            CHAR_ISSUES=$((CHAR_ISSUES + 1))
            WARNINGS=$((WARNINGS + 1))
        fi

        if [ $CHAR_ISSUES -eq 0 ]; then
            echo -e "${GREEN}  ✓ 无特殊字符问题${NC}"
        fi

        echo ""
        IN_MERMAID=0
        CONTENT=""
        continue
    fi

    # 收集 Mermaid 内容
    if [ $IN_MERMAID -eq 1 ]; then
        CONTENT+="$line"$'\n'
    fi
done < "$FILE"

# 总结
echo "=========================================="
echo -e "共检查 ${YELLOW}$BLOCK_NUM${NC} 个图表"
echo -e "致命错误: ${RED}$ERRORS${NC}"
echo -e "警告: ${YELLOW}$WARNINGS${NC}"
echo ""

if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}✓ Mermaid 语法检查通过${NC}"
    [ $WARNINGS -gt 0 ] && echo -e "${YELLOW}建议优化警告项以提升渲染质量${NC}"
    exit 0
else
    echo -e "${RED}✗ 发现致命错误，VSCode 预览可能失败${NC}"
    echo -e "详细规范：~/.claude/skills/markdown-writer/SKILL.md"
    exit 1
fi
