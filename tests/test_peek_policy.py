"""Exercise the shipped selector and persisted cursor with coalesced cards."""
import json
from pathlib import Path
import subprocess
import unittest


class PeekPolicyTests(unittest.TestCase):
    def test_repeats_new_faults_recurrence_and_legacy_cards(self):
        source = (Path(__file__).parents[1] / 'hub/web/assets/porthole.js').read_text()
        helper = source[source.index('  function stationdeskPeekTime'):source.index('  var FLOAT_SIZE')]
        program = helper + r"""
const assert = require('node:assert/strict');
const first = {card_id:'fault',peek:true,read_at:null,level:'urgent',last_at:'01',peek_at:'01'};
const seen = {last_at:'00',card_id:''};
assert.equal(stationdeskSelectPeeks([first],seen,[]).cards.length,1);
const shown = {last_at:stationdeskPeekTime(first),card_id:first.card_id};
const repeat = {...first,last_at:'02',count:2};
assert.equal(stationdeskSelectPeeks([repeat],shown,[]).cards.length,0);
assert.equal(stationdeskSelectPeeks([repeat],JSON.parse(JSON.stringify(shown)),[]).cards.length,0);
assert.equal(stationdeskSelectPeeks([repeat],seen,[first]).cards.length,1);
for (const card of [{...repeat,peek_at:'03'}, {...repeat,card_id:'new',peek_at:'03'},
                    {...repeat,peek_at:'04'}]) {
  assert.equal(stationdeskSelectPeeks([card],shown,[]).cards.length,1);
}
assert.equal(stationdeskSelectPeeks([{...repeat,peek_at:'03',read_at:'03'}],shown,[]).cards.length,0);
const legacy = {...first,last_at:'05'}; delete legacy.peek_at;
assert.equal(stationdeskSelectPeeks([legacy],shown,[]).cards.length,1);
assert.equal(stationdeskSelectPeeks([repeat],{last_at:'02',card_id:'fault'},[]).cards.length,0);
assert.deepEqual(stationdeskSelectPeeks([repeat],null,[]),{seen:{last_at:'01',card_id:'fault'},cards:[]});
console.log('coalesced repeat / reload / queued duplicate / changed fault / new card / recurrence / legacy cursor PASS');
"""
        result = subprocess.run(['node', '-e', program], text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
