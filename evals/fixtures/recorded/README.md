# Recorded eval runs

Real agent runs land here, one folder per `<case>/<config>/`, when you run `slate evals run --live --record`. Each holds
the agent's transcript, `meta.json` (how the run ended, what the sandbox held), the child-process probe's log and the
BROWSER tripwire's log, scrubbed of key-shaped strings and your home folder's path. `slate evals run` grades them for
free. Until the first live run this folder is empty, and every pair reports "no recording yet".
