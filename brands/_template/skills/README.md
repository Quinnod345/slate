# The brand's own agent skills

Skills that only mean something for this brand live here, inside its pack, not in Slate's shared skills: its design
taste, its house style for writing, the way its product must look on screen. They travel with the pack and stay
wherever the pack stays (a company brand's pack, and so its skills, stays on this Mac).

Each skill is a folder with a `SKILL.md` in the open Agent Skills format:

```
skills/<skill-name>/SKILL.md          frontmatter (name, description), then the instructions
skills/<skill-name>/references/       deeper material the skill points to, by relative path
```

Keep the wording agent-neutral (no tool names only one agent has). `slate brand show <name>` lists a pack's skills, and
`slate brand validate <name>` checks each has a SKILL.md with a name and a description.
