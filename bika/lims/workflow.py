from Products.CMFCore.WorkflowCore import WorkflowException
from Products.CMFCore.utils import getToolByName


def skip(instance, action, peek=False, unskip=False):
    """Returns True if the transition is to be SKIPPED

        peek - True just checks the value, does not set.
        unskip - remove skip key (for manual overrides).

    called with only (instance, action_id), this will set the request variable preventing the
    cascade's from re-transitioning the object and return None.
    """

    uid = callable(instance.UID) and instance.UID() or instance.UID
    skipkey = "%s_%s" % (uid, action)
    if 'workflow_skiplist' not in instance.REQUEST:
        if not peek and not unskip:
            instance.REQUEST['workflow_skiplist'] = [skipkey, ]
    else:
        if skipkey in instance.REQUEST['workflow_skiplist']:
            if unskip:
                instance.REQUEST['workflow_skiplist'].remove(skipkey)
            else:
                return True
        else:
            if not peek and not unskip:
                instance.REQUEST["workflow_skiplist"].append(skipkey)


def doActionFor(instance, action_id):
    workflow = getToolByName(instance, "portal_workflow")
    if not skip(instance, action_id, peek=True):
        try:
            workflow.doActionFor(instance, action_id)
        except WorkflowException:
            pass


def default(self, state_info):
    # Delegate to action on instance
    action_id = state_info['transition'].getId()
    prefix = 'workflow_script_'
    method_id = prefix + action_id
    method = getattr(state_info['object'], method_id, None)
    if method is not None:
        method(state_info)
