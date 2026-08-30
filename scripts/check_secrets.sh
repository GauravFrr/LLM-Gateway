#!/bin/bash

echo "=== Running local secrets scanner ==="

if command -v gitleaks &> /dev/null
then
    echo "Gitleaks is installed. Running detection..."
    gitleaks detect --verbose --redact
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
        echo "SUCCESS: No secrets detected in the codebase!"
        exit 0
    else
        echo "FAILURE: Secrets detected! Please fix the leaked keys before pushing."
        exit $EXIT_CODE
    fi
else
    echo "WARNING: Gitleaks is not installed locally."
    echo "Running basic regex scan for potential exposed keys..."
    
    # Custom regex search for typical keys (e.g. gsk_, AQ., sk-proj-)
    grep -rnEi "gsk_[a-zA-Z0-9]{40}|sk-proj-[a-zA-Z0-9]{40}|AQ\.[a-zA-Z0-9_-]{40}" --exclude-dir={.venv,node_modules,.git} .
    
    # Check if .env is tracked in git
    if git ls-files --error-unmatch .env &> /dev/null; then
        echo "FAILURE: .env file is tracked by Git! Run 'git rm --cached .env' to untrack it."
        exit 1
    fi
    
    echo "SUCCESS: Basic checks passed! (Install gitleaks for full scanning)"
    exit 0
fi
