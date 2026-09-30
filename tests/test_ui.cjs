const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const path = require('path');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const inline=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x=>x[1]);
inline.forEach(x=>new vm.Script(x));
const a=html.indexOf('  function isTranslationLead(');
const b=html.indexOf('  function intlOnly(',a);
const scope=vm.createContext({});vm.runInContext(html.slice(a,b),scope);
const proof={url:'https://example.org/2026',text:'Спикер из Китая',edition_year:'2026'};
const base={country:'Россия',date:'2026-10-06',translationStatus:'potential',translationEvidence:[proof]};
const cases=[
 [base,true],
 [{...base,country:'Беларусь'},false],
 [{...base,country:'Германия'},false],
 [{...base,translationStatus:'unknown',audience:'intl'},false],
 [{...base,translationEvidence:[]},false],
 [{...base,date:'2027-10-06'},false],
];
for (const [e,want] of cases) {scope.e=e;assert.equal(vm.runInContext('isTranslationLead(e)',scope),want);}
assert.equal((html.match(/onlyIntl && !isTranslationLead\(e\)/g)||[]).length,2,'list and counters use the same filter');
assert(!html.includes('if (onlyIntl && e.audience !== "intl")'));
console.log('UI syntax and 6 translation-filter scenarios passed.');
