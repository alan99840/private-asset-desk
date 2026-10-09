"""V2.3 wraps the selected V2.2 execution logic with independently checked context."""
import argparse
import json
from copy import deepcopy
from pathlib import Path
from advisor import evaluate as core, default
from context import HERE, read_json, entry_context

def evaluate(packet, market=None, research=None):
    packet = deepcopy(packet)
    result = core(packet)
    # Holding protection and exit reminders must never wait for news or index data.
    if result['status'] not in ['CONDITIONAL_PLAN', 'ENTRY_CONDITIONAL']:
        return result
    ctx = entry_context(packet, market if market is not None else read_json(HERE/'state/market.json'),
                        research if research is not None else read_json(HERE/'state/research.json'))
    result['asset_context_note'] = ctx['note']
    result['impact_events'] = ctx['events']
    for card in result['cards']:
        if card.get('kind') != 'INITIAL':
            continue
        reason = ctx['block']
        if card['symbol'] in ctx['adverse_symbols']:
            reason = '已核验重大负面公司事件；暂停该股票新增风险'
        if reason:
            card['status'] = 'WAIT'
            card['reason'] = [reason]
            # Withhold an actionable numeric entry card when context fails.
            for key in ['quantity','trigger','entry_limit','valid_until_ET','valid_until_HK']:
                card.pop(key, None)
            result['status'] = 'DATA_BLOCKED'
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = evaluate(json.loads(Path(args.input).read_text()))
    text = json.dumps(result, ensure_ascii=False, indent=2, default=default)
    if args.output: Path(args.output).write_text(text+'\n')
    print(text)
