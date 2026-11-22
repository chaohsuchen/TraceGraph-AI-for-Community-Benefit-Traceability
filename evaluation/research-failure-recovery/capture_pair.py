"""Capture one search response for a paired parser replay without issuing a second search."""
import json
import sys
from pathlib import Path
import traceback
from unittest.mock import patch

runtime, case_path, key_path, workspace = map(Path, sys.argv[1:])
sys.path.insert(0,str(runtime))
from tracegraph.experimental_prompt import _SEARCH_SYSTEM
from tracegraph.openai_provider import OpenAIClient, OpenAIWebResearcher
from tracegraph.runtime import read_credential
case=case_path.parent.name
out=workspace / (case+"-paired-response.json")
if out.exists():
    raise FileExistsError(out)
r=json.loads(case_path.read_text(encoding="utf-8"))
client=OpenAIClient(read_credential(key_path,provider="openai"), "gpt-5.4")
original=client._request
def capture(*args, **kwargs):
    result=original(*args, **kwargs)
    saved={"status":result["status"],"usage":result.get("usage"),"object":"response","output":[]}
    for item in result["output"]:
        if item["type"]=="web_search_call":
            action=item.get("action") or {}
            filtered={k:action[k] for k in ("type","url") if k in action}
            if isinstance(action.get("sources"),list):
                filtered["sources"]=[{"url":s["url"]} for s in action["sources"] if s.get("url")]
            saved["output"].append({"type":item["type"],"status":item.get("status"),"action":filtered})
        elif item["type"]=="message":
            saved["output"].append({"type":"message","content":item["content"]})
    with out.open("x",encoding="utf-8") as f:
        json.dump(saved,f,ensure_ascii=False,indent=1)
    return result
try:
    with patch.object(client,"_request",capture):
        result=OpenAIWebResearcher(client)._search({"kind":"DISCOVER","target":r["query"]},system=_SEARCH_SYSTEM,max_uses=4)
    print("before",len(result[1]))
except Exception as e:
    print("before",type(e).__name__,traceback.extract_tb(e.__traceback__)[-1].name)
print(case,out.name)
