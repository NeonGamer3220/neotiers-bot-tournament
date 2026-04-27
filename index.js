require('dotenv').config();
const { Client, GatewayIntentBits, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, StringSelectMenuBuilder, ModalBuilder, TextInputBuilder, TextInputStyle, ChannelType } = require('discord.js');
const { createClient } = require('@supabase/supabase-js');

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
  ],
});

const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_ANON_KEY);

client.once('ready', () => {
  console.log(`Logged in as ${client.user.tag}!`);
});

client.on('interactionCreate', async (interaction) => {
  if (interaction.isCommand()) {
    if (interaction.commandName === 'tournamentqueue') {
      const name = interaction.options.getString('name');
      const timestampStr = interaction.options.getString('timestamp');
      // Assume timestamp is unix as string, e.g., '1777464000'
      const endTime = parseInt(timestampStr.replace(/<t:(\d+):R>/, '$1'));

      const embed = new EmbedBuilder()
        .setTitle(`${name} Tournament`)
        .setDescription(`Csatlakozási határidő: <t:${endTime}:R>`)
        .addFields({ name: 'Játékosok:', value: 'None yet' })
        .setColor('Green');

      const row = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
          .setCustomId('join_tournament_placeholder')
          .setLabel('Belépés a tournamentbe')
          .setStyle(ButtonStyle.Primary),
        new ButtonBuilder()
          .setCustomId('leave_tournament_placeholder')
          .setLabel('Kilépés a tournamentből')
          .setStyle(ButtonStyle.Secondary)
      );

      await interaction.reply({ embeds: [embed], components: [row] });
      const message = await interaction.fetchReply();

      // Insert tournament
      const { data, error } = await supabase
        .from('tournaments')
        .insert({
          name,
          end_time: new Date(endTime * 1000).toISOString(),
          queue_message_id: message.id,
          status: 'open',
          guild_id: interaction.guild.id,
          current_round: 0,
          players: [],
        })
        .select();

      if (error) {
        console.error(error);
        return;
      }

      const tournamentId = data[0].id;

      // Update buttons with tournamentId
      const updatedRow = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
          .setCustomId(`join_tournament_${tournamentId}`)
          .setLabel('Belépés a tournamentbe')
          .setStyle(ButtonStyle.Primary),
        new ButtonBuilder()
          .setCustomId(`leave_tournament_${tournamentId}`)
          .setLabel('Kilépés a tournamentből')
          .setStyle(ButtonStyle.Secondary)
      );

      await message.edit({ embeds: [embed], components: [updatedRow] });

      // Set timer
      const delay = endTime * 1000 - Date.now();
      if (delay > 0) {
        setTimeout(() => startTournament(tournamentId), delay);
      }
    }
  } else if (interaction.isButton()) {
    const customId = interaction.customId;
    if (customId.startsWith('join_tournament_')) {
      const tournamentId = customId.split('_')[2];
      // Check linked
      const { data: linked } = await supabase
        .from('linked_accounts')
        .select('minecraft_username')
        .eq('discord_id', interaction.user.id)
        .single();
      if (!linked) {
        return interaction.reply({ content: 'You must link your Minecraft account first.', ephemeral: true });
      }
      // Get tournament
      const { data: tournament } = await supabase
        .from('tournaments')
        .select('*')
        .eq('id', tournamentId)
        .single();
      if (!tournament || tournament.status !== 'open') {
        return interaction.reply({ content: 'Tournament not open.', ephemeral: true });
      }
      // Check if already joined
      const alreadyJoined = tournament.players.some(p => p.discord_id === interaction.user.id);
      if (alreadyJoined) {
        return interaction.reply({ content: 'You already joined.', ephemeral: true });
      }
      // Add player
      const newPlayers = [...tournament.players, { discord_id: interaction.user.id, minecraft_username: linked.minecraft_username }];
      await supabase
        .from('tournaments')
        .update({ players: newPlayers })
        .eq('id', tournamentId);
      // Update embed
      const channel = interaction.channel;
      const message = await channel.messages.fetch(tournament.queue_message_id);
      const embed = message.embeds[0];
      const playersList = newPlayers.map(p => `<@${p.discord_id}> (${p.minecraft_username})`).join('\n') || 'None yet';
      const updatedEmbed = EmbedBuilder.from(embed).setFields({ name: 'Játékosok:', value: playersList });
      await message.edit({ embeds: [updatedEmbed] });
      await interaction.reply({ content: 'Joined the tournament!', ephemeral: true });
    } else if (customId.startsWith('leave_tournament_')) {
      const tournamentId = customId.split('_')[2];
      const { data: tournament } = await supabase
        .from('tournaments')
        .select('*')
        .eq('id', tournamentId)
        .single();
      if (!tournament || tournament.status !== 'open') {
        return interaction.reply({ content: 'Tournament not open.', ephemeral: true });
      }
      const newPlayers = tournament.players.filter(p => p.discord_id !== interaction.user.id);
      await supabase
        .from('tournaments')
        .update({ players: newPlayers })
        .eq('id', tournamentId);
      // Update embed
      const channel = interaction.channel;
      const message = await channel.messages.fetch(tournament.queue_message_id);
      const embed = message.embeds[0];
      const playersList = newPlayers.map(p => `<@${p.discord_id}> (${p.minecraft_username})`).join('\n') || 'None yet';
      const updatedEmbed = EmbedBuilder.from(embed).setFields({ name: 'Játékosok:', value: playersList });
      await message.edit({ embeds: [updatedEmbed] });
      await interaction.reply({ content: 'Left the tournament!', ephemeral: true });
    } else if (customId.startsWith('close_ticket_')) {
      const parts = customId.split('_');
      const tournamentId = parts[2];
      const p1 = parts[3];
      const p2 = parts[4];
      const { data: match } = await supabase
        .from('matches')
        .select('ticket_channel_id')
        .eq('tournament_id', tournamentId)
        .eq('player1', p1)
        .eq('player2', p2)
        .single();
      if (match) {
        const channel = await client.channels.fetch(match.ticket_channel_id);
        await channel.delete();
      }
      await interaction.reply({ content: 'Ticket closed.', ephemeral: true });
    } else if (customId.startsWith('result_')) {
      const parts = customId.split('_');
      const tournamentId = parts[1];
      const p1 = parts[2];
      const p2 = parts[3];
      const select = new StringSelectMenuBuilder()
        .setCustomId(`select_winner_${tournamentId}_${p1}_${p2}`)
        .setPlaceholder('Select winner')
        .addOptions(
          { label: `${p1} won`, value: `${p1}_win` },
          { label: `${p2} won`, value: `${p2}_win` }
        );
      const row = new ActionRowBuilder().addComponents(select);
      await interaction.reply({ content: 'Select the winner:', components: [row], ephemeral: true });
    }
  } else if (interaction.isStringSelectMenu()) {
    const customId = interaction.customId;
    if (customId.startsWith('select_winner_')) {
      const parts = customId.split('_');
      const tournamentId = parts[2];
      const p1 = parts[3];
      const p2 = parts[4];
      const winner = interaction.values[0].split('_')[0];
      const modal = new ModalBuilder()
        .setCustomId(`score_modal_${tournamentId}_${p1}_${p2}_${winner}`)
        .setTitle('Enter Score')
        .addComponents(
          new ActionRowBuilder().addComponents(
            new TextInputBuilder()
              .setCustomId('score')
              .setLabel('Score (e.g., 7-3)')
              .setStyle(TextInputStyle.Short)
              .setRequired(true)
          )
        );
      await interaction.showModal(modal);
    }
  } else if (interaction.isModalSubmit()) {
    const customId = interaction.customId;
    if (customId.startsWith('score_modal_')) {
      const parts = customId.split('_');
      const tournamentId = parts[2];
      const p1 = parts[3];
      const p2 = parts[4];
      const winner = parts[5];
      const score = interaction.fields.getTextInputValue('score');
      // Update match
      await supabase
        .from('matches')
        .update({ winner, score })
        .eq('tournament_id', tournamentId)
        .eq('player1', p1)
        .eq('player2', p2);
      // Post to results
      const resultsChannel = await client.channels.fetch(process.env.RESULTS_CHANNEL_ID);
      await resultsChannel.send(`${p1} vs ${p2}: ${winner} won ${score}`);
      // Delete ticket
      const { data: match } = await supabase
        .from('matches')
        .select('ticket_channel_id')
        .eq('tournament_id', tournamentId)
        .eq('player1', p1)
        .eq('player2', p2)
        .single();
      if (match) {
        const channel = await client.channels.fetch(match.ticket_channel_id);
        await channel.delete();
      }
      // Check if round complete
      await checkRoundComplete(tournamentId);
      await interaction.reply({ content: 'Result submitted.', ephemeral: true });
    }
  }
});

async function startTournament(tournamentId) {
  const { data: tournament } = await supabase
    .from('tournaments')
    .select('*')
    .eq('id', tournamentId)
    .single();
  if (!tournament || tournament.status !== 'open') return;
  await supabase
    .from('tournaments')
    .update({ status: 'active', current_round: 1 })
    .eq('id', tournamentId);
  const players = tournament.players;
  const shuffled = [...players].sort(() => Math.random() - 0.5);
  const matches = [];
  for (let i = 0; i < shuffled.length; i += 2) {
    if (i + 1 < shuffled.length) {
      matches.push({ p1: shuffled[i], p2: shuffled[i + 1] });
    }
  }
  const guild = client.guilds.cache.get(tournament.guild_id);
  const category = await guild.channels.fetch(process.env.TICKET_CATEGORY_ID);
  for (const match of matches) {
    const channelName = `t-r1-${match.p1.minecraft_username}-${match.p2.minecraft_username}`;
    const channel = await guild.channels.create({
      name: channelName,
      type: ChannelType.GuildText,
      parent: category.id,
      permissionOverwrites: [
        { id: guild.id, deny: ['ViewChannel'] },
        { id: match.p1.discord_id, allow: ['ViewChannel'] },
        { id: match.p2.discord_id, allow: ['ViewChannel'] },
      ],
    });
    const embed = new EmbedBuilder()
      .setTitle('Tournament 1. kör')
      .setDescription(`${match.p1.minecraft_username} vs ${match.p2.minecraft_username}\nSok sikert!`)
      .setColor('Blue');
    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId(`close_ticket_${tournamentId}_${match.p1.minecraft_username}_${match.p2.minecraft_username}`)
        .setLabel('Close ticket')
        .setStyle(ButtonStyle.Danger),
      new ButtonBuilder()
        .setCustomId(`result_${tournamentId}_${match.p1.minecraft_username}_${match.p2.minecraft_username}`)
        .setLabel('eredmény beírása')
        .setStyle(ButtonStyle.Primary)
    );
    await channel.send({ embeds: [embed], components: [row] });
    await supabase
      .from('matches')
      .insert({
        tournament_id: tournamentId,
        round: 1,
        player1: match.p1.minecraft_username,
        player2: match.p2.minecraft_username,
        ticket_channel_id: channel.id,
      });
  }
}

async function startRound(tournamentId, round) {
  const { data: tournament } = await supabase
    .from('tournaments')
    .select('*')
    .eq('id', tournamentId)
    .single();
  if (!tournament) return;
  const players = tournament.players;
  const shuffled = [...players].sort(() => Math.random() - 0.5);
  const matches = [];
  for (let i = 0; i < shuffled.length; i += 2) {
    if (i + 1 < shuffled.length) {
      matches.push({ p1: shuffled[i], p2: shuffled[i + 1] });
    }
  }
  const guild = client.guilds.cache.get(tournament.guild_id);
  const category = await guild.channels.fetch(process.env.TICKET_CATEGORY_ID);
  for (const match of matches) {
    const channelName = `t-r${round}-${match.p1.minecraft_username}-${match.p2.minecraft_username}`;
    const channel = await guild.channels.create({
      name: channelName,
      type: ChannelType.GuildText,
      parent: category.id,
      permissionOverwrites: [
        { id: guild.id, deny: ['ViewChannel'] },
        { id: match.p1.discord_id, allow: ['ViewChannel'] },
        { id: match.p2.discord_id, allow: ['ViewChannel'] },
      ],
    });
    const embed = new EmbedBuilder()
      .setTitle(`Tournament ${round}. kör`)
      .setDescription(`${match.p1.minecraft_username} vs ${match.p2.minecraft_username}\nSok sikert!`)
      .setColor('Blue');
    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId(`close_ticket_${tournamentId}_${match.p1.minecraft_username}_${match.p2.minecraft_username}`)
        .setLabel('Close ticket')
        .setStyle(ButtonStyle.Danger),
      new ButtonBuilder()
        .setCustomId(`result_${tournamentId}_${match.p1.minecraft_username}_${match.p2.minecraft_username}`)
        .setLabel('eredmény beírása')
        .setStyle(ButtonStyle.Primary)
    );
    await channel.send({ embeds: [embed], components: [row] });
    await supabase
      .from('matches')
      .insert({
        tournament_id: tournamentId,
        round,
        player1: match.p1.minecraft_username,
        player2: match.p2.minecraft_username,
        ticket_channel_id: channel.id,
      });
  }
}

async function checkRoundComplete(tournamentId) {
  const { data: tournament } = await supabase
    .from('tournaments')
    .select('*')
    .eq('id', tournamentId)
    .single();
  const round = tournament.current_round;
  const { data: matches } = await supabase
    .from('matches')
    .select('*')
    .eq('tournament_id', tournamentId)
    .eq('round', round);
  const allDone = matches.every(m => m.winner);
  if (allDone) {
    const winners = matches.map(m => m.winner);
    if (winners.length === 1) {
      // Final winner
      const resultsChannel = await client.channels.fetch(process.env.RESULTS_CHANNEL_ID);
      await resultsChannel.send(`Tournament winner: ${winners[0]}`);
      await supabase
        .from('tournaments')
        .update({ status: 'finished' })
        .eq('id', tournamentId);
      return;
    }
    // Get winners with discord_id
    const winnersWithDiscord = await Promise.all(
      winners.map(async (w) => {
        const { data } = await supabase
          .from('linked_accounts')
          .select('discord_id')
          .eq('minecraft_username', w)
          .single();
        return { discord_id: data.discord_id, minecraft_username: w };
      })
    );
    await supabase
      .from('tournaments')
      .update({ players: winnersWithDiscord, current_round: round + 1 })
      .eq('id', tournamentId);
    // Schedule next round
    setTimeout(() => startRound(tournamentId, round + 1), 24 * 60 * 60 * 1000);
  }
}

client.login(process.env.DISCORD_TOKEN);