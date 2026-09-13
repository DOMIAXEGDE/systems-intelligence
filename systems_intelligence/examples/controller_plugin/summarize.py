"""Runs only after reviewing and activating this plugin's content digest."""
contexts = list(controller.iter_entities({'kinds':['context'], 'enabled':True}))
controller.emit('plugin.context-notes.inspected', {'count':len(contexts)})
lines = [f"- {entity['state']['title']} (revision {entity['state']['revision']})" for entity in contexts]
result = controller.dispatch('context.append', {
    'title': input_data.get('title', 'Enabled context inventory'),
    'body': '\n'.join(lines) or 'No enabled contexts were present.',
    'enabled': False,
    'tags': 'controller inventory',
})
