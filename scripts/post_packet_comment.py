"""Post the review packet as ONE updated-in-place PR comment (stage 6).
Usage: post_packet_comment.py --pr 482   (reads packet.json in cwd)"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import upsert_marker_comment

pr = sys.argv[sys.argv.index("--pr")+1]
packet = open("packet.json").read()
body = "### Review packet (stage 6 → swarm)\nMachine-readable; humans can ignore.\n```json\n" + packet + "\n```"
upsert_marker_comment(pr, "review-packet", body)
print(f"packet comment upserted on PR #{pr}")
