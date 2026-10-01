from unittest.mock import Mock

import pytest
from approve_sms import approve
from botocore.exceptions import ClientError


def setup_session(repository, verified=True):
    session = Mock()
    cloudformation = Mock()
    cloudformation.describe_stacks.return_value = {
        "Stacks": [
            {
                "Outputs": [
                    {"OutputKey": "UserPoolId", "OutputValue": "pool"},
                    {
                        "OutputKey": "HistoryTableName",
                        "OutputValue": repository.table_name,
                    },
                ]
            }
        ]
    }
    cognito = Mock()
    cognito.admin_get_user.return_value = {
        "UserStatus": "CONFIRMED",
        "UserAttributes": [
            {"Name": "sub", "Value": "first"},
            {"Name": "email_verified", "Value": "true"},
        ],
    }
    sns = Mock()
    sns.list_sms_sandbox_phone_numbers.return_value = {
        "PhoneNumbers": [
            {
                "PhoneNumber": "+14165550123",
                "Status": "Verified" if verified else "Pending",
            }
        ]
    }
    sns.check_if_phone_number_is_opted_out.return_value = {"isOptedOut": False}
    session.client.side_effect = lambda name: {
        "cloudformation": cloudformation,
        "cognito-idp": cognito,
        "sns": sns,
        "dynamodb": repository.client,
    }[name]
    return session, cognito, sns


def test_approval_requires_verification_and_sends_nothing(repository):
    session, _, sns = setup_session(repository, False)
    with pytest.raises(ValueError):
        approve(session, "dev", "person@example.test", "+14165550123")
    assert repository.get("USER#first", "SMS_ACCESS") is None
    sns.publish.assert_not_called()


def test_phone_cannot_be_claimed_by_two_accounts(repository):
    session, cognito, sns = setup_session(repository)
    approve(session, "dev", "person@example.test", "+14165550123")
    assert repository.get("USER#first", "SMS_ACCESS")["approved"]
    assert repository.get("USER#first", "REMINDER") is None
    cognito.admin_get_user.return_value["UserAttributes"][0]["Value"] = "second"
    with pytest.raises(ClientError):
        approve(session, "dev", "other@example.test", "+14165550123")
    assert repository.get("USER#second", "SMS_ACCESS") is None
    sns.publish.assert_not_called()
