(() => {
	var action = new PlugIn.Action(function(selection, sender){
		
		// IDENTIFY KANBAN TAG, CREATE IF MISSING
		var targetTag = flattenedTags.byName("Kanban") || new Tag("Kanban")
		
		// ADD KANBAN CATEGORIES IF MISSING
		var tagTitles = ["Reviewed", "To Do", "In Progress", "Waiting", "Done"]
		tagTitles.forEach(title => {
			if (!targetTag.children.byName(title)){
				new Tag(title, targetTag)
			}
		})
		
		// REORDER THE CATEGORIES
		tagTitles.forEach(title => {
			var tag = targetTag.children.byName(title)
			moveTags([tag], targetTag)
		})
		
		// SHOW THE TAGS
		var tagIDs = targetTag.children.map(tag => tag.id.primaryKey)
		var tagIDsString =  tagIDs.join(",")
		URL.fromString("omnifocus:///tag/" + tagIDsString).open()
		
	});
	
	return action;
})();