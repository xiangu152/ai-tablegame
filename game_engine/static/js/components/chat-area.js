/**
 * chat-area.js — Message list with SSE connection (~90 lines)
 * Props: game, room — reacts to room changes
 */
const ChatArea = {
  template: `
  <div class="chat-area" ref="chatEl">
    <div v-if="messages.length === 0" class="chat-empty">No messages yet. The adventure begins...</div>
    <div v-for="msg in messages" :key="msg.id" class="msg" :class="{ system: msg.from === 'SYSTEM', death: msg.content && msg.content.includes('💀') }">
      <span class="msg-from">[{{ msg.from }}]</span>{{ msg.content }}<span class="msg-time">{{ msg.at }}</span>
    </div>
  </div>`,
  props: { game: String, room: { type: String, default: '酒馆大厅' } },
  emits: ['new-message'],
  data() { return { messages: [], lastId: 0, sse: null }; },
  watch: {
    room() { this.disconnect(); this.load(); this.connect(); },
    game() { this.disconnect(); if (this.game) { this.load(); this.connect(); } },
  },
  async mounted() { if (this.game) { await this.load(); this.connect(); } },
  beforeUnmount() { this.disconnect(); },
  methods: {
    async load() {
      const msgs = await apiGet('/api/games/' + this.game + '/chat?room=' + encodeURIComponent(this.room));
      this.messages = msgs || [];
      if (this.messages.length) this.lastId = this.messages[this.messages.length - 1].id;
    },
    connect() {
      if (!this.game || this.sse) return;
      this.sse = new EventSource(API + '/api/games/' + this.game + '/events');
      this.sse.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.id > this.lastId && msg.room === this.room) {
            this.lastId = msg.id;
            this.messages.push(msg);
            this.$emit('new-message', msg);
            this.$nextTick(() => this._scroll());
          }
        } catch (err) {}
      };
      this.sse.onerror = () => { this.disconnect(); setTimeout(() => this.connect(), 3000); };
    },
    disconnect() { if (this.sse) { this.sse.close(); this.sse = null; } },
    _scroll() {
      const el = this.$refs.chatEl;
      if (el) el.scrollTop = el.scrollHeight;
    },
  },
};
