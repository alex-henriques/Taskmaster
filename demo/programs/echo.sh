#!/bin/sh
echo "echo.sh ready"
while IFS= read -r line; do
    echo "echo: $line"
done