require('dotenv').config();
const { REST } = require('@discordjs/rest');
const { Routes } = require('discord-api-types/v9');
const { SlashCommandBuilder } = require('@discordjs/builders');

const commands = [
  new SlashCommandBuilder()
    .setName('tournamentqueue')
    .setDescription('Create a tournament queue')
    .addStringOption(option =>
      option.setName('name')
        .setDescription('Tournament name')
        .setRequired(true))
    .addStringOption(option =>
      option.setName('timestamp')
        .setDescription('Timestamp for join deadline (unix)')
        .setRequired(true)),
];

const rest = new REST({ version: '9' }).setToken(process.env.DISCORD_TOKEN);

(async () => {
  try {
    console.log('Started refreshing application (/) commands.');

    await rest.put(
      Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID),
      { body: commands },
    );

    console.log('Successfully reloaded application (/) commands.');
  } catch (error) {
    console.error(error);
  }
})();