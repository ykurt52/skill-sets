# skill-sets

Claude Code skills for hardware, firmware and FPGA work. Each skill is
packaged as its own plugin, so you install only the ones you need.

## Skills

| Plugin | Category | What it does |
| --- | --- | --- |
| [hw-read](plugins/hardware/hw-read/README.md) | hardware | Reads a KiCad or Altium project into JSON: parts, nets, rules, stackup, layout |
| [sch-review](plugins/hardware/sch-review/README.md) | hardware | Schematic review: netlist checks plus a datasheet pass per IC |
| [emc-precheck](plugins/hardware/emc-precheck/README.md) | hardware | Port protection pre-check with a board view, plus surge, burst and ESD pulse simulation |
| [pcb-review](plugins/hardware/pcb-review/README.md) | hardware | Layout review: decoupling, pairs, reference planes, isolation, with an HTML board view |
| [hw-notes](plugins/hardware/hw-notes/README.md) | hardware | Bilingual commit messages and Redmine descriptions for Altium repos |

## Install

### Claude Code in a terminal

Add the marketplace once:

```
/plugin marketplace add ykurt52/skill-sets
```

Then install a single skill:

```
/plugin install hw-notes@skill-sets
```

From a local clone, use the folder path instead:

```
/plugin marketplace add ./skill-sets
```

### Claude desktop app (Code tab)

The desktop plugin manager lists plugins only from marketplaces that are
already known. Declare the marketplace and the plugins you want in
`~/.claude/settings.json` (`%USERPROFILE%\.claude\settings.json` on
Windows), merging with any keys already there:

```json
{
  "extraKnownMarketplaces": {
    "skill-sets": {
      "source": { "source": "github", "repo": "ykurt52/skill-sets" }
    }
  },
  "enabledPlugins": {
    "hw-read@skill-sets": true,
    "sch-review@skill-sets": true
  }
}
```

Start a new session. The marketplace is cloned in the background and the
enabled plugins are installed. Manage them later under **+ > Plugins**.
`sch-review`, `pcb-review` and `emc-precheck` need `hw-read`, so enable it
with them.

### Running a skill

A skill runs as `/<plugin>:<skill>`, for example `/hw-notes:hw-notes`.

## Layout

```
.claude-plugin/marketplace.json     list of all plugins
plugins/
  <category>/                       hardware, firmware, fpga, ...
    <plugin>/
      .claude-plugin/plugin.json    plugin manifest
      README.md                     what it does, how to run it
      skills/<skill>/
        SKILL.md
        scripts/, styles/, ...      files used only by this skill
```

Rules:

- One skill per plugin. A plugin never reads files from another plugin.
- Everything a skill needs lives under its own `skills/<skill>/` folder.
- Every plugin has a README with install, run, input, output and an example.

## Adding a skill

1. Create `plugins/<category>/<name>/skills/<name>/SKILL.md`.
2. Add `plugins/<category>/<name>/.claude-plugin/plugin.json` with at
   least `name`.
3. Add `plugins/<category>/<name>/README.md`.
4. Add an entry to `.claude-plugin/marketplace.json` with
   `"source": "./plugins/<category>/<name>"`.
5. Add a row to the table above.
6. Check it:

```
claude plugin validate .
claude plugin validate ./plugins/<category>/<name> --strict
```

## License

MIT, see [LICENSE](LICENSE).
