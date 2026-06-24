/**
 * chat-area.js — Message list with SSE + room switching + streaming effect (~110 lines)
 * Props: game, room — reacts to room changes
 */
const ChatArea = {
  template: `
  <div class="chat-area" ref="chatEl">
    <div v-if="messages.length === 0" class="chat-empty">No messages yet. The adventure begins...</div>
    <div v-for="msg in messages" :key="msg.id" class="msg" :class="{ system: msg.from === 'SYSTEM', death: msg.content && msg.content.includes('💀'), streaming: msg._streaming }">
      <span class="msg-from">[{{ msg.from }}]</span>
      <span v-if="msg._streaming">{{ msg._display }}<span class="cursor-blink">▌</span></span>
      <span v-else>{{ msg.content }}</span>
      <span class="msg-time">{{ msg.at }}</span>
    </div>
  </div>`,
  props: { game: String, room: { type: String, default: '酒馆大厅' } },
  emits: ['new-message'],
  data() { return { messages: [], lastId: 0, sse: null, typewriters: {} }; },
  watch: {
    room() { this.disconnect(); this.load(); this.connect(); },
    game() { this.disconnect(); if (this.game) { this.load(); this.connect(); } },
  },
  async mounted() { if (this.game) { await this.load(); this.connect(); } },
  beforeUnmount() { this.disconnect(); },
  methods: {
    async load() {
      const msgs = await apiGet('/api/games/' + this.game + '/chat?room=' + encodeURIComponent(this.room));
      this.messages = (msgs || []).map(m => ({ ...m, _display: m.content, _streaming: false }));
      if (this.messages.length) this.lastId = this.messages[this.messages.length - 1].id;
      this.$nextTick(() => this._scroll());
    },
    connect() {
      if (!this.game || this.sse) return;
      const url = API + '/api/games/' + this.game + '/events?room=' + encodeURIComponent(this.room);
      this.sse = new EventSource(url);
      this.sse.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);

          if (data.type === 'chat') {
            // Filter by current room
            if (data.room !== this.room) return;
            if (data.id > this.lastId) {
              this.lastId = data.id;
              const msg = { ...data, _display: '', _streaming: true };
              this.messages.push(msg);
              this.$emit('new-message', msg);
              this._typewrite(msg);
              this.$nextTick(() => this._scroll());
            }
          }
          // Ignore other event types (players, etc.) handled by parent
        } catch (err) {}
      };
      this.sse.onerror = () => { this.disconnect(); setTimeout(() => this.connect(), 3000); };
    },
    disconnect() { if (this.sse) { this.sse.close(); this.sse = null; } },
    _typewrite(msg) {
      const full = msg.content || '';
      let i = 0;
      const speed = full.length > 100 ? 8 : full.length > 40 ? 15 : 25;
      const timer = setInterval(() => {
        i += 1;
        msg._display = full.slice(0, i);
        if (i >= full.length) {
          clearInterval(timer);
          msg._streaming = false;
          msg._display = full;
        }
      }, speed);
    },
    _scroll() {
      const el = this.$refs.chatEl;
      if (el) el.scrollTop = el.scrollHeight;
    },
  },
};
