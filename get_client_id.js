const { Client, GatewayIntentBits } = require('discord.js');
require('dotenv').config();

const client = new Client({ intents: [] });

client.once('ready', () => {
  console.log('Client ID:', client.user.id);
  process.exit();
});

client.login(process.env.DISCORD_TOKEN);