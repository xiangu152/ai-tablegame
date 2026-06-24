/**
 * utils.js — Shared helpers, API, constants (~80 lines)
 * All components depend on this. Load FIRST.
 */
const API = '';

async function apiGet(path) {
  try { const r = await fetch(API + path); return r.ok ? r.json() : null; }
  catch (e) { console.error('GET', path, e); return null; }
}
async function apiPost(path, body) {
  try {
    const r = await fetch(API + path, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    return r.ok ? r.json() : null;
  } catch (e) { console.error('POST', path, e); return null; }
}

const TEMPLATES = {
  fighter: { race:'Human',class_:'Fighter',level:1,
    abilities:{str:16,dex:14,con:15,int:10,wis:12,cha:8},
    combat:{hp_max:12,hp_current:12,ac:18,initiative:2,speed:30,hit_dice:'1d10',hit_dice_remaining:1,death_saves:{successes:0,failures:0},hp_temp:0},
    weapons:[{name:'Longsword',attack:'1d20+5',damage:'1d8+3'}],skill_proficiencies:['Athletics','Intimidation'] },
  wizard: { race:'High Elf',class_:'Wizard',level:1,
    abilities:{str:8,dex:14,con:12,int:17,wis:13,cha:10},
    combat:{hp_max:7,hp_current:7,ac:12,initiative:2,speed:30,hit_dice:'1d6',hit_dice_remaining:1,death_saves:{successes:0,failures:0},hp_temp:0},
    spells:['Magic Missile','Mage Armor','Burning Hands'],skill_proficiencies:['Arcana','Investigation'] },
  rogue: { race:'Halfling',class_:'Rogue',level:1,
    abilities:{str:8,dex:17,con:14,int:12,wis:10,cha:13},
    combat:{hp_max:9,hp_current:9,ac:14,initiative:3,speed:25,hit_dice:'1d8',hit_dice_remaining:1,death_saves:{successes:0,failures:0},hp_temp:0},
    weapons:[{name:'Dagger',attack:'1d20+5',damage:'1d4+3'}],skill_proficiencies:['Stealth','Sleight of Hand','Perception'] },
  cleric: { race:'Dwarf',class_:'Cleric',level:1,
    abilities:{str:14,dex:10,con:15,int:10,wis:16,cha:12},
    combat:{hp_max:10,hp_current:10,ac:16,initiative:0,speed:25,hit_dice:'1d8',hit_dice_remaining:1,death_saves:{successes:0,failures:0},hp_temp:0},
    spells:['Cure Wounds','Guiding Bolt','Bless'],skill_proficiencies:['Medicine','Religion'] },
};

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function roomIcon(type) { return type === 'public' ? '🌐' : type === 'team' ? '👥' : '🔒'; }
function hpClass(pct) { return pct < 30 ? 'low' : pct < 60 ? 'mid' : 'good'; }
