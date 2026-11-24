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
const base={country:'Россия',date:'2026-10-06',audience:'intl',translationStatus:'declared',translationEvidence:[]};
const cases=[
 [base,true],
 [{...base,country:'Беларусь'},false],
 [{...base,country:'Германия'},false],
 [{...base,translationStatus:'unknown',audience:'intl'},true],
 [{...base,translationEvidence:[]},true],
 [{...base,audience:'unknown'},false],
];
for (const [e,want] of cases) {scope.e=e;assert.equal(vm.runInContext('isTranslationLead(e)',scope),want);}
assert.match(html,/var scoped = filtered\(true\).filter\(inView\)/,'counters reuse the list filters');
const geoStart = html.indexOf('  function geography(');
const geoEnd = html.indexOf('  function normalizeType(',geoStart);
const sections = {abroad:{open:false}, unknown:{open:false}};
const geo = vm.createContext({$: id => sections[id]});
vm.runInContext(html.slice(geoStart,geoEnd),geo);
assert.equal(geo.inView({country:'Россия',audience:'intl'}),true);
assert.equal(geo.inView({country:'Китай'}),false);
assert.equal(geo.inView({country:''}),false);
sections.abroad.open = true;
assert.equal(geo.inView({country:'Китай'}),true);
assert.equal(geo.inView({country:''}),false);
assert(!html.includes('if (onlyIntl && e.audience !== "intl")'));
console.log('UI syntax and 6 translation-filter scenarios passed.');
