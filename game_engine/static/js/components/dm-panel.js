/**
 * dm-panel.js — DM tabs: characters, saves (~150 lines)
 */
const DmPanel = {
  template: `
  <div>
    <div class="tab-bar">
      <button v-for="t in tabs" :key="t.id" class="tab-btn" :class="{ active: tab === t.id }" @click="tab = t.id">{{ t.label }}</button>
    </div>
    <div v-if="tab === 'chars'" class="tab-content">
      <div class="card">
        <h3 style="color:#e94560;margin-bottom:8px">Create Character</h3>
        <div class="form-row">
          <input v-model="charName" placeholder="Character name"><input v-model="charPlayer" placeholder="Player name">
          <button class="btn btn-primary" @click="createChar">Create</button>
        </div>
        <select v-model="charTpl" style="margin-top:8px">
          <option value="">-- Select template --</option>
          <option value="fighter">Fighter (STR+CON)</option>
          <option value="wizard">Wizard (INT)</option>
          <option value="rogue">Rogue (DEX)</option>
          <option value="cleric">Cleric (WIS)</option>
        </select>
      </div>
      <div v-if="players.length === 0" style="color:#666;padding:12px">No characters created yet.</div>
      <div v-for="p in players" :key="p.name" class="char-row">
        <div>
          <b>{{ p.name }}</b> <span style="color:#888">Lv.{{ p.level }} {{ p.race }} {{ p.class }} | HP {{ p.hp }} | AC {{ p.ac }}</span>
          <span class="hp-bar"><span class="hp-fill" :class="hpClass(p.hp_pct)" :style="{ width: p.hp_pct + '%' }"></span></span>
        </div>
        <button class="btn btn-danger btn-small" @click="deleteChar(p.name)">Delete</button>
      </div>
    </div>
    <div v-if="tab === 'saves'" class="tab-content">
      <div class="card">
        <h3 style="color:#e94560;margin-bottom:8px">Save Management</h3>
        <div class="form-row">
          <input v-model="saveLabel" placeholder="Save label">
          <button class="btn btn-primary" @click="doSave">💾 Save</button>
          <button class="btn btn-secondary" @click="doLoad('_auto')">📂 Load Auto-Save</button>
        </div>
        <div v-if="saves.length === 0" style="color:#666;margin-top:12px">No saves yet.</div>
        <div v-for="s in saves" :key="s.label || s.save_name" class="save-row">
          <span>📁 {{ s.label || s.save_name }} <span style="color:#666;font-size:11px">{{ (s.created_at || '').slice(0, 16) }}</span></span>
          <button class="btn btn-secondary btn-small" @click="doLoad(s.label || s.save_name)">Load</button>
        </div>
      </div>
    </div>
  </div>`,
  props: { game: String },
  data() {
    return {
      tab: 'chars', tabs: [{id:'chars',label:'Characters'},{id:'saves',label:'Saves'}],
      charName: '', charPlayer: '', charTpl: '', saveLabel: '',
      players: [], saves: [],
    };
  },
  watch: {
    game() { if (this.game) this.fetch(); },
    tab() { if (this.game) this.fetch(); },
  },
  async mounted() { if (this.game) await this.fetch(); },
  methods: {
    async fetch() {
      if (this.tab === 'chars') this.players = await apiGet('/api/games/' + this.game + '/players') || [];
      else this.saves = await apiGet('/api/games/' + this.game + '/saves') || [];
    },
    async createChar() {
      const name = this.charName.trim(); if (!name) return alert('Enter name');
      const tpl = this.charTpl && TEMPLATES[this.charTpl] ? {...TEMPLATES[this.charTpl]} : {...TEMPLATES.fighter};
      tpl.name = name; tpl.player_name = this.charPlayer.trim() || 'Player';
      await apiPost('/api/games/' + this.game + '/players/create', tpl);
      this.charName = ''; this.charPlayer = ''; this.charTpl = '';
      await this.fetch();
    },
    async deleteChar(name) {
      if (!confirm('Delete ' + name + '?')) return;
      await apiPost('/api/games/' + this.game + '/players/delete', { name });
      await this.fetch();
    },
    async doSave() {
      const label = this.saveLabel.trim() || 'Manual Save';
      this.$loading.show('Saving checkpoint...');
      await apiPost('/api/games/' + this.game + '/save', { label });
      this.$loading.hide();
      this.saveLabel = '';
      await this.fetch();
    },
    async doLoad(label) {
      this.$loading.show('Loading checkpoint...');
      await apiPost('/api/games/' + this.game + '/load', { label });
      this.$loading.hide();
      await this.fetch();
    },
  },
};
