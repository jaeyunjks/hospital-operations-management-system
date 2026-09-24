#!/usr/bin/env bash
# Terminal validation of the shared RAG server. Start server.py first, then run
# from the repository root:  bash ai-services/rag-server/validate.sh
# Exits non-zero if any command fails or returns an unexpected status.
PY=${PY:-python3}
failures=0
run() {
  local shown=""; for a in "$@"; do case "$a" in *" "*) shown+=" \"$a\"";; *) shown+=" $a";; esac; done
  echo "\$ python3 ai-services/rag-server/cli.py$shown"; "$PY" ai-services/rag-server/cli.py "$@"; local code=$?; echo "(exit $code)"; echo; [ $code -eq 0 ] || failures=$((failures + 1))
}
echo "# HOMS shared RAG server - terminal validation"
echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
run health
run sources --feature student-3
run retrieve "Who can write off an expired batch?" --feature student-3
run query "Who can write off an expired batch?" --feature student-3 --expect answered
run query "How does FEFO issuing work?" --feature student-3 --expect answered
run query "What does the scheduled agent do and can it approve orders?" --feature student-3 --expect answered
run query "What port does the pharmacy backend use?" --feature student-3 --expect answered
run query "What is the recommended paracetamol dose for a child?" --feature student-3 --expect insufficient_context
run query "What is the capital of France?" --feature student-3 --expect insufficient_context
run query "How many beds are free in the emergency ward?" --feature student-3 --expect insufficient_context
echo "# ${failures} failed command(s)"
exit $((failures > 0))
