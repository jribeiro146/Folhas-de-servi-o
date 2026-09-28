const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../../src/web/static/js/maintenance-model.js');

// A compact definition exercises the branching contract independently of the full text catalogue.
const definition = {periods:{monthly:'Mensal',annual:'Anual',other:'Outra'},groups:{conventional:'Central convencional',repeater:'Repetidor'},
 signature_exceptions:{customer:{field:'customer_not_present'}},
 fields:{conventional:[['brand','Marca','text'],['total','Zonas','number'],['used','Zonas em uso','number']],repeater:[['brand','Marca','text'],['location','Local do repetidor','text']]},
 general:[['G','Geral']],conventional:[['C','Central']],repeater:[['R','Repetidor']],peripherals:[['P','Periférico']],trials:[['T','Ensaio']]};
const answers = key => ({[key]:{answer:'OK',justification:''}});
const complete = () => ({location:'Local fictício',date:'2026-09-17',technician:'Técnico fictício',period:'annual',
 general:answers('G'),peripherals:answers('P'),trials:answers('T'),configuration:{conventional:true,repeater:false},conventional:[{brand:'DEMO',total:'2',used:'1',checks:answers('C')}],repeater:[],signatures:{}});

test('only maintenance plus SADI activates checklists',()=>{
 assert.equal(model.applicable({service_types:{manutencao:true},equipments:{sadi:true}}),true);
 assert.equal(model.applicable({service_types:{assistencia:true},equipments:{sadi:true}}),false);
});
test('NC needs its own explanation; justified NC can be signed',()=>{
 const site=complete();site.conventional[0].checks.C.answer='NC';
 assert.match(model.validateSite(site,definition)[0],/justificação NC/);
 site.conventional[0].checks.C.justification='Exemplo fictício';
 assert.deepEqual(model.validateSite(site,definition),[]);
 assert.equal(model.status(site,definition),'Por assinar');
});
test('peripherals and coverage are shared and remain required without centrals',()=>{
 const site=complete();site.period='monthly';site.conventional.push(structuredClone(site.conventional[0]));
 assert.equal(model.validateSite(site,definition).length,1);
 site.coverage_percent='0';assert.deepEqual(model.validateSite(site,definition),[]);
 site.trials.T.answer='';assert.equal(model.validateSite(site,definition).length,1);
 site.configuration.conventional=false;assert.equal(model.validateSite(site,definition).length,1);
 site.trials.T.answer='NA';assert.deepEqual(model.validateSite(site,definition),[]);
 assert.equal(site.conventional.length,2);
});
test('shared NC requires justification and each repeater requires its location',()=>{
 const site=complete();site.peripherals.P.answer='NC';
 site.configuration.repeater=true;site.repeater=[{brand:'DEMO',checks:answers('R')}];
 assert.equal(model.validateSite(site,definition).length,2);
 site.repeater[0].location='Receção';site.peripherals.P.justification='Exemplo fictício';
 assert.deepEqual(model.validateSite(site,definition),[]);
});
test('bounds, dates and signatures affect completion without departure time',()=>{
 const site=complete();site.conventional[0].used='3';site.date='2026-02-30';
 assert.equal(model.validateSite(site,definition).length,2);
 const signed=complete();signed.signatures={customer:{token:'one'},technician:{token:'two'}};
 assert.equal(model.status(signed,definition),'Completa');
});

test('invalid photographs and duplicate IDs block signing but optional notes do not',()=>{
 const site=complete();site.final_observations='Observação de teste';
 assert.deepEqual(model.validateSite(site,definition),[]);
 const photo={id:'photo-test-123',image:'data:image/jpeg;base64,eA==',error:'Imagem inválida'};
 site.photos=[photo];assert.match(model.validateSite(site,definition)[0],/Fotografia/);
 site.photos=[{...photo,error:'',image:'https://example.invalid/image.jpg'}];assert.match(model.validateSite(site,definition)[0],/imagem inválida/);
 site.photos=[{...photo,error:''},{...photo,error:''}];assert.match(model.validateSite(site,definition)[0],/repetido/);
});

test('customer absence never waives the technician signature or checklist completion',()=>{
 const site=complete(), other=complete();
 site.customer_not_present=true;
 assert.deepEqual(model.signatureErrors(site,definition),['Assinatura do técnico']);
 assert.equal(model.status(site,definition),'Por assinar');
 site.technician_signature_not_collected=true;
 assert.deepEqual(model.signatureErrors(site,definition),['Assinatura do técnico']);
 site.signatures.technician={token:'signed'};
 assert.deepEqual(model.signatureErrors(site,definition),[]);
 assert.equal(model.status(site,definition),'Completa');
 assert.equal(model.status(other,definition),'Por assinar');
 site.technician='';assert.ok(model.validateSite(site,definition).includes('Técnico de serviço'));
 assert.equal(model.status(site,definition),'Em preenchimento');
 site.customer_not_present=false;
 assert.deepEqual(model.signatureErrors(site,definition),['Assinatura do cliente']);
});

test('strings and numbers cannot dispense with checklist signatures',()=>{
 for(const value of ['true','false',1,null]){
   const site=complete();site.customer_not_present=value;site.technician_signature_not_collected=value;
   assert.equal(model.signatureErrors(site,definition).length,2);
 }
});
