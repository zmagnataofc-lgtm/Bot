import discord
from discord.ext import commands
from discord import ui
import json
import os
import time
from collections import defaultdict

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "apollo_data.json"
CONFIG_FILE = "apollo_config.json"

# Carregar dados
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"produtos": {}, "estoque": {}, "vendas": [], "cupons": {}, "afiliados": {}, "saldo": {}, "estoques_conteudo": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"log_channel": None, "preco": {}}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

data = load_data()
config = load_config()

# Anti-fraude: controle de tickets por usuário
ticket_cooldown = defaultdict(list)

def is_adm():
    async def predicate(ctx):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

async def send_log(guild, embed):
    if config["log_channel"]:
        channel = guild.get_channel(config["log_channel"])
        if channel:
            await channel.send(embed=embed)

# Botões de pagamento
class PagarSaldoButton(ui.Button):
    def __init__(self, produto):
        super().__init__(label="Pagar com Saldo", style=discord.ButtonStyle.green)
        self.produto = produto
    async def callback(self, interaction: discord.Interaction):
        await processar_compra(interaction, self.produto, "saldo")

class PagarTicketButton(ui.Button):
    def __init__(self, produto):
        super().__init__(label="Abrir Ticket", style=discord.ButtonStyle.blurple)
        self.produto = produto
    async def callback(self, interaction: discord.Interaction):
        await processar_compra(interaction, self.produto, "ticket")

async def processar_compra(interaction, produto, tipo_pagamento):
    user_id = str(interaction.user.id)

    # Anti-fraude: max 3 tickets em 5min
    agora = time.time()
    ticket_cooldown[user_id] = [t for t in ticket_cooldown[user_id] if agora - t < 300]
    if len(ticket_cooldown[user_id]) >= 3:
        await interaction.response.send_message("🚨 Anti-Fraude: Aguarde 5 minutos para abrir outro ticket", ephemeral=True)
        return
    ticket_cooldown[user_id].append(agora)

    if data["estoque"].get(produto, 0) <= 0:
        await interaction.response.send_message("❌ Sem estoque desse produto", ephemeral=True)
        return

    preco = config["preco"].get(produto, 0)

    # Pagamento com saldo
    if tipo_pagamento == "saldo":
        if data["saldo"].get(user_id, 0) < preco:
            await interaction.response.send_message(f"❌ Saldo insuficiente. Você tem R${data['saldo'].get(user_id,0)}", ephemeral=True)
            return
        data["saldo"][user_id] -= preco

    # Cria ticket
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True)
    }
    channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", overwrites=overwrites)

    # Entrega automática
    conteudo = data["estoques_conteudo"].get(produto, []).pop(0) if data["estoques_conteudo"].get(produto) else "Produto sem conteúdo"
    data["estoque"][produto] -= 1
    data["vendas"].append({"user": str(interaction.user), "produto": produto, "preco": preco, "tipo": tipo_pagamento})
    save_data(data)

    await channel.send(f"Olá {interaction.user.mention}! Aqui está seu **{produto}**\n```{conteudo}```\nObrigado pela compra!")

    # Log
    embed_log = discord.Embed(title="💰 Nova Venda", color=0x00ff00)
    embed_log.add_field(name="Cliente", value=interaction.user.mention)
    embed_log.add_field(name="Produto", value=produto)
    embed_log.add_field(name="Valor", value=f"R${preco}")
    embed_log.add_field(name="Pagamento", value=tipo_pagamento)
    await send_log(guild, embed_log)

    await interaction.response.send_message(f"✅ Ticket criado: {channel.mention}", ephemeral=True)

class PainelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for produto in data["produtos"].keys():
            self.add_item(ComprarButton(produto))

class ComprarButton(ui.Button):
    def __init__(self, produto):
        super().__init__(label=f"{produto} - R${config['preco'].get(produto,0)}", style=discord.ButtonStyle.green)
        self.produto = produto
    async def callback(self, interaction: discord.Interaction):
        view = ui.View()
        view.add_item(PagarSaldoButton(self.produto))
        view.add_item(PagarTicketButton(self.produto))
        await interaction.response.send_message(f"Como deseja pagar **{self.produto}**?", view=view, ephemeral=True)

# COMANDOS ADM
@bot.command()
@is_adm()
async def setlog(ctx, canal: discord.TextChannel):
    """Define canal de logs:!setlog #logs-vendas"""
    config["log_channel"] = canal.id
    save_config(config)
    await ctx.send(f"✅ Canal de logs definido para {canal.mention}")

@bot.command()
@is_adm()
async def addproduto(ctx, nome, preco: float, *, descricao):
    """Adiciona produto:!addproduto Nitro 25.00 Conta Nitro 1 mes"""
    data["produtos"][nome] = descricao
    data["estoque"][nome] = 0
    data["estoques_conteudo"][nome] = []
    config["preco"][nome] = preco
    save_data(data); save_config(config)
    await ctx.send(f"✅ Produto **{nome}** R${preco} adicionado!")

@bot.command()
@is_adm()
async def addestoque(ctx, produto, quantidade: int, *, conteudo):
    """Adiciona estoque:!addestoque Nitro 5 login:senha"""
    data["estoque"][produto] = data["estoque"].get(produto, 0) + quantidade
    data["estoques_conteudo"].setdefault(produto, []).extend([conteudo]*quantidade)
    save_data(data)
    await ctx.send(f"✅ {quantidade}x **{produto}** adicionado")

@bot.command()
@is_adm()
async def addsaldo(ctx, membro: discord.Member, valor: float):
    """Adiciona saldo:!addsaldo @user 100"""
    data["saldo"][str(membro.id)] = data["saldo"].get(str(membro.id), 0) + valor
    save_data(data)
    await ctx.send(f"✅ R${valor} adicionado ao saldo de {membro.mention}")
    await membro.send(f"💰 Seu saldo foi atualizado! Novo saldo: R${data['saldo'][str(membro.id)]}")

@bot.command()
async def saldo(ctx):
    """Ver seu saldo"""
    s = data["saldo"].get(str(ctx.author.id), 0)
    await ctx.send(f"💰 Seu saldo atual: **R${s}**")

@bot.command()
@is_adm()
async def estoque(ctx):
    embed = discord.Embed(title="📦 Painel de Estoque", color=0x3498db)
    for p, q in data["estoque"].items():
        embed.add_field(name=f"{p} - R${config['preco'].get(p,0)}", value=f"Qtd: {q}", inline=True)
    await ctx.send(embed=embed)

@bot.command()
@is_adm()
async def painel(ctx):
    embed = discord.Embed(title="🛒 Apollo Vendas", description="Escolha um produto abaixo:", color=0x00ff00)
    for p, desc in data["produtos"].items():
        embed.add_field(name=f"{p} - R${config['preco'].get(p,0)}", value=f"{desc} | Estoque: {data['estoque'].get(p,0)}", inline=False)
    await ctx.send(embed=embed, view=PainelView())

@bot.event
async def on_ready():
    print(f"Apollo v2 online como {bot.user}")

if __name__ == "__main__":
    import os
    TOKEN = os.getenv("TOKEN")
    bot.run(TOKEN)
